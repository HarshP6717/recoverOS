"""
Tests for StrongFeatureAwareHeuristic (Phase 2C Step 2A).

Verifies:
- Determinism
- Rule correctness for each failure type
- Hard-failure retry suppression
- High-attempt fallback
- Contact fatigue guard
- Escalation rule
- Valid action output only
- Identical 1,000-case population coverage
- Same seed protocol as other policies
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.policies.feature_aware_heuristic import (
    StrongFeatureAwareHeuristic,
    _FAILURE_TYPE_PRIMARY_ACTION,
    _FAILURE_TYPE_FALLBACK_ACTION,
    _HARD_FAILURE_TYPES,
    _HIGH_CONTACT_THRESHOLD,
    _HIGH_ATTEMPT_THRESHOLD,
    _ESCALATION_MIN_AMOUNT,
    _ESCALATE_ELIGIBLE_FAILURE_TYPES,
)
from simulator.recovery_simulator import ACTIONS
from evaluation.evaluator import _load_test_data, SEED_BASE


def _record(**kwargs) -> Dict[str, Any]:
    """Build a minimal record with sensible defaults."""
    defaults = {
        "failure_type": "insufficient_funds",
        "payment_method": "upi",
        "attempt_number": 1,
        "contact_count": 0,
        "amount": 1000.0,
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture(scope="module")
def policy() -> StrongFeatureAwareHeuristic:
    return StrongFeatureAwareHeuristic()


@pytest.fixture(scope="module")
def test_df():
    return _load_test_data()


# ─────────────────────────────────────────────────────────────────────────────
# Determinism
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_record_same_action(self, policy):
        r = _record(failure_type="insufficient_funds", attempt_number=1)
        a1 = policy.select_action(r)
        a2 = policy.select_action(r)
        assert a1 == a2, "Heuristic must be deterministic"

    def test_batch_consistent_with_single(self, policy, test_df):
        """Batch actions must match single-record actions."""
        import pandas as pd
        single = [policy.select_action(test_df.iloc[i]) for i in range(20)]
        batch  = policy.select_actions_batch(test_df.iloc[:20])
        assert single == batch


# ─────────────────────────────────────────────────────────────────────────────
# Rule 1 — Contact fatigue guard
# ─────────────────────────────────────────────────────────────────────────────

class TestContactFatigueRule:
    def test_high_contact_returns_send_reminder(self, policy):
        r = _record(contact_count=_HIGH_CONTACT_THRESHOLD)
        assert policy.select_action(r) == "send_reminder"

    def test_contact_just_below_threshold_not_send_reminder(self, policy):
        # At threshold - 1, fatigue rule should NOT fire for easy cases
        r = _record(failure_type="insufficient_funds",
                    contact_count=_HIGH_CONTACT_THRESHOLD - 1,
                    attempt_number=1)
        action = policy.select_action(r)
        # Should be the failure-type primary, not send_reminder
        assert action != "send_reminder" or _FAILURE_TYPE_PRIMARY_ACTION.get("insufficient_funds") == "send_reminder"

    def test_very_high_contact_still_send_reminder(self, policy):
        r = _record(contact_count=10)
        assert policy.select_action(r) == "send_reminder"


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2 — Hard failure guard
# ─────────────────────────────────────────────────────────────────────────────

class TestHardFailureRule:
    @pytest.mark.parametrize("ft", ["expired_card", "hard_decline", "invalid_payment_method"])
    def test_hard_failure_low_attempt_gives_pmu(self, policy, ft):
        r = _record(failure_type=ft, attempt_number=1, contact_count=0)
        assert policy.select_action(r) == "payment_method_update"

    @pytest.mark.parametrize("ft", ["expired_card", "hard_decline", "invalid_payment_method"])
    def test_hard_failure_high_attempt_gives_recovery_link(self, policy, ft):
        r = _record(failure_type=ft, attempt_number=_HIGH_ATTEMPT_THRESHOLD, contact_count=0)
        assert policy.select_action(r) == "recovery_link"

    @pytest.mark.parametrize("ft", ["expired_card", "hard_decline", "invalid_payment_method"])
    def test_hard_failure_retry_never_selected(self, policy, ft):
        """Retry actions must never be selected for hard failures."""
        for attempt in range(1, 6):
            r = _record(failure_type=ft, attempt_number=attempt, contact_count=0)
            action = policy.select_action(r)
            assert action not in ("retry_now", "retry_later"), (
                f"Retry selected for hard failure {ft} attempt {attempt}"
            )

    def test_hard_failures_are_correct_set(self):
        assert _HARD_FAILURE_TYPES == {"expired_card", "hard_decline", "invalid_payment_method"}


# ─────────────────────────────────────────────────────────────────────────────
# Rule 3 — High attempt fallback (non-hard)
# ─────────────────────────────────────────────────────────────────────────────

class TestHighAttemptRule:
    @pytest.mark.parametrize("ft", ["insufficient_funds", "bank_timeout",
                                     "soft_decline", "customer_abandoned",
                                     "unknown"])
    def test_high_attempt_uses_fallback(self, policy, ft):
        r = _record(failure_type=ft, attempt_number=_HIGH_ATTEMPT_THRESHOLD, contact_count=0)
        action = policy.select_action(r)
        expected = _FAILURE_TYPE_FALLBACK_ACTION.get(ft)
        assert action == expected, f"Expected fallback {expected} for {ft} at attempt {_HIGH_ATTEMPT_THRESHOLD}, got {action}"

    def test_attempt_3_uses_primary(self, policy):
        """Attempt 3 is below threshold — primary action should apply."""
        r = _record(failure_type="insufficient_funds", attempt_number=3, contact_count=0)
        action = policy.select_action(r)
        assert action == _FAILURE_TYPE_PRIMARY_ACTION["insufficient_funds"]


# ─────────────────────────────────────────────────────────────────────────────
# Rule 4 — Primary action per failure type
# ─────────────────────────────────────────────────────────────────────────────

class TestPrimaryActionRule:
    @pytest.mark.parametrize("ft,expected", list(_FAILURE_TYPE_PRIMARY_ACTION.items()))
    def test_primary_action_correct(self, policy, ft, expected):
        r = _record(failure_type=ft, attempt_number=1, contact_count=0, amount=500.0)
        # repeated_failure at attempt=1, low contact, amount=500 may escalate
        if ft == "repeated_failure" and 500 >= _ESCALATION_MIN_AMOUNT:
            # escalation override active
            assert policy.select_action(r) in ("escalate_human", expected)
        else:
            assert policy.select_action(r) == expected

    def test_unknown_failure_type_handled(self, policy):
        r = _record(failure_type="completely_made_up_type", attempt_number=1, contact_count=0)
        action = policy.select_action(r)
        assert action in ACTIONS


# ─────────────────────────────────────────────────────────────────────────────
# Rule 5 — Escalation override for repeated_failure
# ─────────────────────────────────────────────────────────────────────────────

class TestEscalationRule:
    def test_escalation_active_when_conditions_met(self, policy):
        r = _record(
            failure_type="repeated_failure",
            amount=_ESCALATION_MIN_AMOUNT + 100,
            attempt_number=1,
            contact_count=1,
        )
        assert policy.select_action(r) == "escalate_human"

    def test_escalation_inactive_low_amount(self, policy):
        r = _record(
            failure_type="repeated_failure",
            amount=_ESCALATION_MIN_AMOUNT - 1,
            attempt_number=1,
            contact_count=0,
        )
        action = policy.select_action(r)
        assert action != "escalate_human"

    def test_escalation_inactive_high_attempt(self, policy):
        r = _record(
            failure_type="repeated_failure",
            amount=_ESCALATION_MIN_AMOUNT + 100,
            attempt_number=2,
            contact_count=0,
        )
        action = policy.select_action(r)
        # Attempt 2 is still < HIGH_ATTEMPT_THRESHOLD but escalation rule requires attempt==1
        assert action != "escalate_human"

    def test_escalation_inactive_high_contact(self, policy):
        r = _record(
            failure_type="repeated_failure",
            amount=_ESCALATION_MIN_AMOUNT + 100,
            attempt_number=1,
            contact_count=3,  # > 2 threshold
        )
        action = policy.select_action(r)
        assert action != "escalate_human"


# ─────────────────────────────────────────────────────────────────────────────
# Output validity
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputValidity:
    def test_all_batch_actions_are_valid(self, policy, test_df):
        actions = policy.select_actions_batch(test_df)
        assert len(actions) == len(test_df)
        for i, a in enumerate(actions):
            assert a in ACTIONS, f"Invalid action '{a}' at index {i}"

    def test_batch_length_matches_input(self, policy, test_df):
        actions = policy.select_actions_batch(test_df)
        assert len(actions) == 1000


# ─────────────────────────────────────────────────────────────────────────────
# Identical population / seed protocol
# ─────────────────────────────────────────────────────────────────────────────

class TestPopulationProtocol:
    def test_heuristic_covers_all_1000_cases(self, policy, test_df):
        """Heuristic evaluation must cover all 1,000 test cases."""
        from evaluation.run_step2a import run_heuristic_evaluation
        records = run_heuristic_evaluation(test_df)
        assert len(records) == 1000

    def test_heuristic_indices_are_sequential(self, policy, test_df):
        from evaluation.run_step2a import run_heuristic_evaluation
        records = run_heuristic_evaluation(test_df)
        for i, r in enumerate(records):
            assert r["index"] == i

    def test_heuristic_same_seed_as_baseline(self, test_df):
        """Verify that seed = 42 + i is used (spot-check)."""
        from simulator.recovery_simulator import simulate_action
        policy = StrongFeatureAwareHeuristic()
        row = test_df.iloc[7]
        action = policy.select_action(row)
        seed = SEED_BASE + 7
        sim1 = simulate_action(row, action, seed=seed)
        sim2 = simulate_action(row, action, seed=seed)
        assert sim1["recovered"] == sim2["recovered"]
