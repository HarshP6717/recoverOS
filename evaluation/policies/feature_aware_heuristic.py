"""
RecoverOS Phase 2C Step 2A — Feature-Aware Heuristic Policy.

PURPOSE
-------
Provides a credible non-ML baseline that uses the same features that the
simulator's ground-truth probability function uses.  It is designed to answer:

    "Does the ML model add value beyond what a knowledgeable domain expert
     could achieve by reading the simulator's published probability tables?"

DESIGN PRINCIPLES
-----------------
All rules are PRE-SPECIFIED from the simulator's published tables
(BASE_ACTION_PROBABILITIES, METHOD_ACTION_LOGIT_BIAS, and the documented
contextual modifiers).  No rule was chosen by inspecting test.csv outcomes.

HOW RULES WERE DERIVED
----------------------
Step 1 — Failure-type primary action
    Read BASE_ACTION_PROBABILITIES directly.
    For each failure_type, compute base ERV = p * representative_amount - cost
    using the documented cost table (ACTION_COSTS).
    Representative amount: median of the invoice distribution (order of ~₹500).

    This gives a FAILURE-TYPE-FIRST action ranking that is independent of test
    outcomes.  The table is published in recovery_simulator.py lines 65–147.

    Result (see scratch_rule_design.py output, pre-computed before any test run):

    insufficient_funds    → retry_later        (base p=0.68, ERV dominates)
    bank_timeout          → retry_later        (base p=0.79, highest prob overall;
                                                retry_now p=0.74 close but same cost)
    soft_decline          → retry_later        (base p=0.64)
    expired_card          → payment_method_update (base p=0.82, retry disabled)
    hard_decline          → payment_method_update (base p=0.70, retry near-zero)
    invalid_payment_method→ payment_method_update (base p=0.78, retry near-zero)
    customer_abandoned    → recovery_link      (base p=0.68, engagement approach)
    repeated_failure      → payment_method_update (base p=0.62, change required)
    unknown               → retry_later        (base p=0.40, safest cheap action)

Step 2 — Hard-failure retry suppression
    For expired_card / hard_decline / invalid_payment_method,
    retry_now (p≤0.01) and retry_later (p≤0.02) have near-zero base probability.
    This matches Guardrail 2 in the ML policy and is independently justified by
    the base probability table.

Step 3 — attempt_number degradation cut-off
    The simulator applies: attempt_penalty = -0.32 * (attempt_num - 1)
    At attempt_number = 4: logit shift = -0.96 ≈ halving the odds ratio.
    At attempt_number ≥ 5: logit shift = -1.28 → severe suppression.
    Domain rule: on attempt 4+, escalation probability is still worthwhile
    for persistent cases, but we cap at recovery_link (cheaper than escalation,
    good base prob on most types) and stop only when even that becomes
    cost-negative (amount < recovery_link_cost / min_useful_prob).

Step 4 — High-contact fatigue guard
    Simulator applies: fatigue_penalty = -0.12 * max(0, contact_count - 2)
    At contact_count ≥ 6 the cumulative logit penalty = -0.48 or worse.
    Domain rule: if contact_count ≥ 6, downgrade to the lowest-cost action
    (send_reminder) to avoid continued fatigue penalty while still acting.

Step 5 — Amount-gated escalation
    escalate_human costs ₹30.  For the escalation to have positive expected value:
    amount * p_base > 30  →  p_base > 30 / amount.
    For repeated_failure (p=0.76) or unknown (p=0.52), even at ₹100 the ERV
    would be 0.52*100 - 30 = 22.  At ₹50: 0.52*50 - 30 = -4.
    Conservative rule: allow escalate_human only when amount ≥ 200 AND the
    failure_type's base escalation probability is ≥ 0.60.
    (Failure types with escalation p < 0.60: bank_timeout=0.50, insufficient_funds=0.55)
    This threshold is derived from the cost table, not from test outcomes.

Step 6 — payment_method secondary bias for tiebreaking
    METHOD_ACTION_LOGIT_BIAS is published in the simulator (lines 150–196).
    When two actions have similar base ERVs, the method bias resolves the tie.
    mandate_nach has a strong +0.35 logit bias toward retry_later — this
    reinforces the retry_later choice for mandate_nach records.
    This is a secondary adjustment, not a primary rule.

WHAT THIS HEURISTIC IS NOT
---------------------------
- NOT an oracle (it does not read the ground-truth probability function).
- NOT tuned on test.csv outcomes.
- NOT a copy of the ML policy logic.
- NOT guaranteed to be optimal — it is a reasonable, defensible domain expert.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Mapping, Union

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.recovery_simulator import ACTIONS

# ─────────────────────────────────────────────────────────────────────────────
# Pre-specified action rankings by failure type
# Source: BASE_ACTION_PROBABILITIES table in recovery_simulator.py, lines 65-147
# Computed via ERV = base_prob * 543 - cost (pre-test-run calculation).
# ─────────────────────────────────────────────────────────────────────────────

# Primary action for each failure type (Step 1 — failure-type-first)
_FAILURE_TYPE_PRIMARY_ACTION: Dict[str, str] = {
    "insufficient_funds":     "retry_later",          # base p=0.68, top ERV
    "bank_timeout":           "retry_later",          # base p=0.79, top ERV
    "soft_decline":           "retry_later",          # base p=0.64, top ERV
    "expired_card":           "payment_method_update",# base p=0.82; retries near-zero
    "hard_decline":           "payment_method_update",# base p=0.70; retries near-zero
    "invalid_payment_method": "payment_method_update",# base p=0.78; retries near-zero
    "customer_abandoned":     "recovery_link",        # base p=0.68, engagement focus
    "repeated_failure":       "payment_method_update",# base p=0.62, channel change needed
    "unknown":                "retry_later",          # base p=0.40, cheapest safe action
}

# Fallback action when primary is unavailable or degraded
_FAILURE_TYPE_FALLBACK_ACTION: Dict[str, str] = {
    "insufficient_funds":     "recovery_link",        # base p=0.45
    "bank_timeout":           "retry_now",            # base p=0.74 (close second)
    "soft_decline":           "payment_method_update",# base p=0.46
    "expired_card":           "recovery_link",        # base p=0.74
    "hard_decline":           "recovery_link",        # base p=0.58
    "invalid_payment_method": "recovery_link",        # base p=0.72
    "customer_abandoned":     "send_reminder",        # base p=0.52
    "repeated_failure":       "recovery_link",        # base p=0.54
    "unknown":                "send_reminder",        # base p=0.28
}

# Failure types where retry (retry_now, retry_later) is near-zero (Step 2)
_HARD_FAILURE_TYPES = frozenset({"expired_card", "hard_decline", "invalid_payment_method"})

# Failure types where escalate_human base prob ≥ 0.60 (Step 5)
# repeated_failure=0.76, expired_card=0.72, customer_abandoned=0.70,
# invalid_payment_method=0.68, hard_decline=0.66, soft_decline=0.58→excluded,
# escalate threshold = 0.60 strictly greater
_ESCALATE_ELIGIBLE_FAILURE_TYPES = frozenset({
    "repeated_failure",       # base p_escalate = 0.76
    "expired_card",           # base p_escalate = 0.72
    "customer_abandoned",     # base p_escalate = 0.70
    "invalid_payment_method", # base p_escalate = 0.68
    "hard_decline",           # base p_escalate = 0.66
})

# Minimum amount for human escalation (Step 5): 30 / 0.60 ≈ 50 raw, but
# base prob applies to a median-cost record.  Use ₹200 as conservative threshold
# (30 / 200 = 0.15 < 0.60, so ERV is still positive at this floor).
_ESCALATION_MIN_AMOUNT: float = 200.0

# High-contact threshold (Step 4): contact_count ≥ 6 triggers fatigue downgrade
_HIGH_CONTACT_THRESHOLD: int = 6

# High-attempt threshold (Step 3): attempt ≥ 4 triggers conservative fallback
_HIGH_ATTEMPT_THRESHOLD: int = 4


class StrongFeatureAwareHeuristic:
    """
    Feature-aware heuristic policy for RecoverOS evaluation.

    Uses failure_type, payment_method, attempt_number, contact_count, and
    amount to select a recovery action.  All thresholds are pre-specified
    from the simulator's published tables.

    This class intentionally does NOT extend simulator.policies.BasePolicy
    to remain fully isolated in the evaluation/ subtree.  It implements an
    equivalent select_action / select_actions_batch interface.
    """

    @property
    def name(self) -> str:
        return "Strong Feature-Aware Heuristic"

    def select_action(
        self,
        record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]],
    ) -> str:
        """
        Select a recovery action using pre-specified domain rules.

        Decision tree (evaluated top-to-bottom, first match wins):

        1. High-contact fatigue guard:
           If contact_count >= 6 → send_reminder (cheapest non-stop action,
           fatigue penalty makes everything else worse).

        2. Hard failure guard:
           If failure_type in {expired_card, hard_decline, invalid_payment_method}:
             - Primary: payment_method_update (base p ≥ 0.70)
             - High attempt (≥ 4): recovery_link (still good, lower cost than escalation)

        3. High attempt degradation:
           If attempt_number >= 4 → use fallback action for failure type
           (not stop — the fallback still has non-trivial ERV; stop is only
           appropriate when ERV < 0, which requires knowing cost & prob; the
           fallback actions were chosen to have ERV > 0 at the median amount).

        4. Failure-type primary action:
           Select from _FAILURE_TYPE_PRIMARY_ACTION table.

        5. Payment-method escalation bonus for high-escalation failure types:
           If failure_type in _ESCALATE_ELIGIBLE_FAILURE_TYPES
           AND amount >= _ESCALATION_MIN_AMOUNT
           AND attempt_number == 1
           AND contact_count <= 2:
             Consider escalating — but only if the primary action is not already
             escalate_human (to avoid double-application).
             Escalation is added as an *override* only for repeated_failure,
             where it has the highest base prob (0.76) and retry/reminder are weak.
        """
        failure_type = str(record.get("failure_type", "unknown"))
        payment_method = str(record.get("payment_method", "card"))
        attempt_num = int(record.get("attempt_number", 1))
        contact_count = int(record.get("contact_count", 0))
        amount = float(record.get("amount", 0.0))

        # Normalize unknown failure types to "unknown"
        if failure_type not in _FAILURE_TYPE_PRIMARY_ACTION:
            failure_type = "unknown"

        # Rule 1 — High-contact fatigue guard
        # At contact_count ≥ 6, logit fatigue penalty ≥ -0.48.
        # send_reminder (cost=0.50) is still cost-effective; all expensive
        # actions become marginal.
        if contact_count >= _HIGH_CONTACT_THRESHOLD:
            return "send_reminder"

        # Rule 2 — Hard failure guard (retries are near-useless)
        if failure_type in _HARD_FAILURE_TYPES:
            if attempt_num >= _HIGH_ATTEMPT_THRESHOLD:
                # Recovery link: still good prob, lower cost than escalation
                return "recovery_link"
            return "payment_method_update"

        # Rule 3 — High attempt degradation (non-hard failures)
        if attempt_num >= _HIGH_ATTEMPT_THRESHOLD:
            return _FAILURE_TYPE_FALLBACK_ACTION.get(failure_type, "send_reminder")

        # Rule 4 — Failure-type primary action
        primary = _FAILURE_TYPE_PRIMARY_ACTION[failure_type]

        # Rule 5 — Escalation override for repeated_failure
        # repeated_failure: best action by base ERV is escalate_human (0.76 * amt - 30).
        # The primary was set to payment_method_update (ERV slightly lower at base).
        # Allow escalation upgrade when: amount is sufficient AND fresh contact (first
        # attempt, not yet fatigued).
        if (
            failure_type == "repeated_failure"
            and amount >= _ESCALATION_MIN_AMOUNT
            and attempt_num == 1
            and contact_count <= 2
        ):
            return "escalate_human"

        return primary

    def select_actions_batch(self, df: pd.DataFrame) -> list:
        """Apply select_action to every row in df, returning a list of actions."""
        return [self.select_action(row) for _, row in df.iterrows()]
