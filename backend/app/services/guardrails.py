"""
RecoverOS Deterministic Guardrail Engine.

Enforces business, risk, and regulatory guardrails on recovery candidate actions:
- G1: Negative or zero Expected Recovery Value (ERV) suppression.
- G2: Permanent credential/authorization failure retry suppression.
- G3: Micro-amount high-cost human escalation suppression (< ₹100.00).
- G4: Phase-2 production customer fatigue and dunning cap guardrail.
- G5: Safe degraded state fallback when models or systems are unavailable.

NOTE: G4 is strictly Phase-2 business-risk / customer-fatigue logic designed for
production safety and was intentionally absent from the Phase-1 economic simulation benchmark.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple
from backend.app.core.config import (
    ACTIONS,
    HIGH_VALUE_THRESHOLD_INR,
    MAX_ATTEMPT_FATIGUE_CAP,
    MAX_CONTACT_FATIGUE_CAP,
    MICRO_AMOUNT_THRESHOLD_INR,
)
from backend.app.schemas.recovery import ActionCandidateEvaluation, DiagnosisRequest


class GuardrailEngine:
    """
    Evaluates candidate actions against deterministic safety and business guardrails.
    """

    HARD_FAILURES: Set[str] = {
        "hard_decline",
        "expired_card",
        "invalid_payment_method",
        "subscription_halted",
    }

    def evaluate_candidates(
        self,
        request: DiagnosisRequest,
        unfiltered_evaluations: Dict[str, Tuple[float, float, float]],
    ) -> Tuple[List[ActionCandidateEvaluation], List[str]]:
        """
        Evaluates all candidate actions against deterministic guardrails.

        Parameters
        ----------
        request : DiagnosisRequest
            Incoming payment failure request.
        unfiltered_evaluations : Dict[str, Tuple[float, float, float]]
            Mapping of action -> (predicted_recovery_probability, action_cost, predicted_erv).

        Returns
        -------
        Tuple[List[ActionCandidateEvaluation], List[str]]
            List of evaluated ActionCandidateEvaluation models and list of triggered guardrail codes.
        """
        evaluations: List[ActionCandidateEvaluation] = []
        triggered_guardrails: Set[str] = set()

        amount = request.amount
        failure_type = request.failure_type.lower()
        attempt_number = request.attempt_number
        contact_count = request.contact_count

        for action in ACTIONS:
            if action not in unfiltered_evaluations:
                continue

            prob, cost, erv = unfiltered_evaluations[action]

            # STOP is treated explicitly as an allowed baseline candidate
            if action == "stop":
                evaluations.append(
                    ActionCandidateEvaluation(
                        action="stop",
                        predicted_recovery_probability=0.0,
                        action_cost=0.0,
                        predicted_erv=0.0,
                        allowed=True,
                        suppression_reason=None,
                    )
                )
                continue

            allowed = True
            reason: Optional[str] = None

            # Guardrail G2: Permanent Failure Retry Suppression
            if failure_type in self.HARD_FAILURES and action in {"retry_now", "retry_later"}:
                allowed = False
                reason = (
                    f"G2: Automated retries suppressed on permanent failure '{failure_type}'"
                )
                triggered_guardrails.add("G2_PERMANENT_FAILURE_RETRY_SUPPRESSION")

            # Guardrail G3: Micro-Amount High-Cost Suppression (< ₹100.00)
            elif amount < MICRO_AMOUNT_THRESHOLD_INR and action == "escalate_human":
                allowed = False
                reason = (
                    f"G3: Human escalation (₹{cost:.2f}) suppressed for invoice amount < ₹{MICRO_AMOUNT_THRESHOLD_INR:.2f}"
                )
                triggered_guardrails.add("G3_MICRO_AMOUNT_HUMAN_SUPPRESSION")

            # Guardrail G4: Phase-2 Customer Fatigue & Dunning Cap
            elif (
                contact_count >= MAX_CONTACT_FATIGUE_CAP
                or attempt_number >= MAX_ATTEMPT_FATIGUE_CAP
            ) and action in {"retry_now", "retry_later", "send_reminder"}:
                allowed = False
                reason = (
                    f"G4: Customer fatigue cap reached (contacts={contact_count}, attempt={attempt_number})"
                )
                triggered_guardrails.add("G4_CUSTOMER_FATIGUE_CAP")

            # Guardrail G6: Halted Subscription Safety (P0-2)
            elif failure_type == "subscription_halted" and action in {"retry_now", "retry_later", "recovery_link"}:
                allowed = False
                reason = "G6: Generic recovery actions blocked on halted subscription. Requires payment update or human escalation."
                triggered_guardrails.add("G6_HALTED_SUBSCRIPTION_SAFETY")

            # Guardrail G1: Negative or zero Expected Recovery Value
            elif erv <= 0.0 and action != "escalate_human":
                allowed = False
                reason = "G1: Negative or zero expected recovery value (ERV <= 0)"
                triggered_guardrails.add("G1_NEGATIVE_ERV")

            evaluations.append(
                ActionCandidateEvaluation(
                    action=action,
                    predicted_recovery_probability=round(prob, 4),
                    action_cost=round(cost, 2),
                    predicted_erv=round(erv, 2),
                    allowed=allowed,
                    suppression_reason=reason,
                )
            )

        return evaluations, sorted(list(triggered_guardrails))

    def select_best_action(
        self,
        evaluations: List[ActionCandidateEvaluation],
    ) -> Tuple[str, str, str]:
        """
        Selects the permitted action with the highest predicted ERV.

        If all recovery actions (non-stop) are suppressed, selects STOP with an explicit reason.

        Returns
        -------
        Tuple[str, str, str]
            (selected_action, decision_status, decision_reason)
        """
        # Filter allowed candidates
        allowed_candidates = [c for c in evaluations if c.allowed]

        if not allowed_candidates:
            # Fallback if even STOP was somehow suppressed
            return (
                "stop",
                "SUPPRESSED_STOP",
                "All recovery candidate actions were suppressed by guardrails. STOP selected.",
            )

        # Check if only 'stop' is allowed or non-stop candidates exist
        non_stop_allowed = [c for c in allowed_candidates if c.action != "stop"]

        if not non_stop_allowed:
            return (
                "stop",
                "SUPPRESSED_STOP",
                "All active recovery actions were suppressed by deterministic guardrails. Safe STOP selected.",
            )

        # Pick candidate with highest predicted ERV
        best_candidate = max(non_stop_allowed, key=lambda c: c.predicted_erv)

        if best_candidate.predicted_erv <= 0.0 and best_candidate.action != "escalate_human":
            return (
                "stop",
                "SUPPRESSED_STOP",
                f"Maximum permitted ERV (₹{best_candidate.predicted_erv:.2f}) is non-positive. STOP selected.",
            )

        return (
            best_candidate.action,
            "SUCCESS",
            f"Action '{best_candidate.action}' selected with highest permitted predicted ERV (₹{best_candidate.predicted_erv:.2f}).",
        )

    def get_degraded_state_fallback(
        self,
        request: DiagnosisRequest,
        failure_reason: str,
    ) -> Tuple[List[ActionCandidateEvaluation], str, str, str, List[str]]:
        """
        Constructs a safe fallback decision when the ML model or scoring engine fails.
        Guarantees no automatic destructive/financial charges are triggered.
        """
        amount = request.amount
        fallback_action = "escalate_human" if amount >= HIGH_VALUE_THRESHOLD_INR else "send_reminder"

        candidate_evals = []
        for a in ACTIONS:
            candidate_evals.append(
                ActionCandidateEvaluation(
                    action=a,
                    predicted_recovery_probability=0.0,
                    action_cost=0.0,
                    predicted_erv=0.0,
                    allowed=(a == fallback_action or a == "stop"),
                    suppression_reason=(
                        "Model unavailable - automated financial actions suppressed"
                        if a != fallback_action and a != "stop"
                        else None
                    ),
                )
            )

        decision_reason = f"Safe fallback '{fallback_action}' selected due to degraded state: {failure_reason}"
        return (
            candidate_evals,
            fallback_action,
            "FALLBACK_SAFE",
            decision_reason,
            ["G5_DEGRADED_STATE_FALLBACK"],
        )
