"""
RecoverOS Economic Decision Engine (Checkpoint 3).

Replaces the legacy ML model with the AI-driven DiagnosisEngine.
Calculates Expected Recovery Value (ERV) for each candidate action:
ERV = (Recovery Probability * Amount) - Action Cost - Customer Friction Penalty

Enforces strict deterministic guardrails over the AI's diagnosis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from backend.app.core.config import (
    ACTIONS,
    ACTION_COSTS,
    ACTION_FRICTION_COSTS,
    MODEL_VERSION,
)
from backend.app.schemas.recovery import (
    ActionCandidateEvaluation,
    DiagnosisRequest,
    DiagnosisResponse as OrchestratorDiagnosisResponse,
    CounterfactualData,
)
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.schemas.diagnosis import DiagnosisRequest as IntelligenceDiagnosisRequest

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Core economic decision engine responsible for scoring candidate recovery actions
    using ERV and applying deterministic guardrails.
    """

    def __init__(
        self,
        guardrail_engine: getattr(logging, 'Optional', None) = None,  # lazy typing avoidance
        diagnosis_engine: getattr(logging, 'Optional', None) = None,
    ):
        self.guardrail_engine = guardrail_engine or GuardrailEngine()
        self.diagnosis_engine = diagnosis_engine or DiagnosisEngine()
        self.model_version = "diagnosis_erv_v2"

    def _map_probability(self, action: str, probs: Dict[str, float]) -> float:
        """
        Maps the detailed system action to the high-level AI probability output.
        """
        if action in ("recovery_link", "send_reminder", "payment_method_update"):
            return probs.get("payment_link", 0.0)
        elif action in ("retry_now", "retry_later"):
            return probs.get("retry", 0.0)
        elif action == "escalate_human":
            return probs.get("escalate", 0.0)
        elif action == "stop":
            return probs.get("no_action", 0.0)
        return 0.0

    def evaluate_request(
        self,
        request: DiagnosisRequest,
    ) -> Tuple[List[ActionCandidateEvaluation], str, str, str, List[str]]:
        """
        Executes the full evaluation pipeline:
        1. Call AI DiagnosisEngine for failure root cause and probabilities.
        2. ERV calculation: (amount * P_pred) - direct_cost - friction_cost.
        3. Deterministic guardrail evaluation (including AI confidence checks).
        4. Optimal permitted action selection based on highest ERV.

        Returns
        -------
        Tuple[List[ActionCandidateEvaluation], str, str, str, List[str], Optional[CounterfactualData]]
            (candidate_evaluations, selected_action, decision_status, decision_reason, guardrails_triggered, counterfactual_data)
        """
        # 1. AI Diagnosis
        intel_req = IntelligenceDiagnosisRequest(
            failure_reason=request.failure_type,
            payment_amount=request.amount,
            payment_method=request.payment_method,
            customer_history=f"CLV: {request.customer_lifetime_value}, Failures: {request.previous_failure_count}",
            previous_attempts=request.attempt_number - 1,
            days_overdue=request.days_overdue,
            journey_round=request.attempt_number,
        )
        
        diagnosis = self.diagnosis_engine.diagnose_failure(intel_req)
        
        # 2. ERV Calculation
        amount = float(request.amount)
        unfiltered_evaluations: Dict[str, Tuple[float, float, float]] = {}

        for action in ACTIONS:
            if action == "stop":
                p = 0.0
                cost = 0.0
                erv = 0.0
                friction = 0.0
            else:
                p = self._map_probability(action, diagnosis.recovery_probabilities)
                cost = float(ACTION_COSTS.get(action, 0.0))
                friction = float(ACTION_FRICTION_COSTS.get(action, 0.0))
                erv = float((amount * p) - cost - friction)

            unfiltered_evaluations[action] = (p, cost, erv)

        # 3. Guardrail Enforcement
        evaluations, triggered_guardrails = self.guardrail_engine.evaluate_candidates(
            request, unfiltered_evaluations
        )
        
        # Low Confidence Guardrail (AI specific override)
        if diagnosis.confidence < 0.60:
            triggered_guardrails.append("LOW_AI_CONFIDENCE")
            # Force escalate or stop if AI is unsure
            for ev in evaluations:
                if ev.action not in ("escalate_human", "stop"):
                    ev.allowed = False
                    ev.suppression_reason = "AI confidence < 0.60. Action blocked."

        # Negative ERV Guardrail
        for ev in evaluations:
            if ev.predicted_erv <= 0.0 and ev.action not in ("stop", "escalate_human"):
                ev.allowed = False
                ev.suppression_reason = "Expected Recovery Value (ERV) is negative."
                if "NEGATIVE_ERV_PROTECTION" not in triggered_guardrails:
                    triggered_guardrails.append("NEGATIVE_ERV_PROTECTION")

        # 4. Action Selection
        selected_action, status, base_reason = self.guardrail_engine.select_best_action(
            evaluations
        )

        # 5. Explainability Formatting
        if diagnosis.failure_category == "unknown" and diagnosis.confidence <= 0.1:
            status = "FALLBACK_SAFE"
            base_reason = "Provider unavailable. Degraded state safe fallback applied."

        decision_reason = (
            f"Diagnosis: {diagnosis.failure_category} (conf: {diagnosis.confidence:.2f}). "
            f"Reasoning: {diagnosis.reasoning_summary} "
            f"Result: {base_reason}"
        )

        # 6. Counterfactual Simulation
        # Rigorously determine what would have been chosen if the selected action was unavailable.
        counterfactual_evals = [ev for ev in evaluations if ev.action != selected_action]
        cf_action, _, _ = self.guardrail_engine.select_best_action(counterfactual_evals)
        
        selected_ev = next((e for e in evaluations if e.action == selected_action), None)
        cf_ev = next((e for e in evaluations if e.action == cf_action), None)
        
        counterfactual_data = None
        if selected_ev and cf_ev:
            counterfactual_data = CounterfactualData(
                selected_action=selected_ev.action,
                selected_erv=selected_ev.predicted_erv,
                selected_probability=selected_ev.predicted_recovery_probability,
                counterfactual_action=cf_ev.action,
                counterfactual_erv=cf_ev.predicted_erv,
                counterfactual_probability=cf_ev.predicted_recovery_probability,
                value_difference=round(selected_ev.predicted_erv - cf_ev.predicted_erv, 2),
                guardrails_applied=triggered_guardrails.copy(),
            )

        return evaluations, selected_action, status, decision_reason, triggered_guardrails, counterfactual_data

    def diagnose(self, request: DiagnosisRequest) -> OrchestratorDiagnosisResponse:
        """
        Performs diagnosis and returns candidate scoring breakdown without ledger persistence.
        """
        evals, action, status, reason, guardrails, counterfactuals = self.evaluate_request(request)
        return OrchestratorDiagnosisResponse(
            transaction_id=request.transaction_id,
            amount=request.amount,
            recommended_action=action,
            decision_status=status,
            decision_reason=reason,
            guardrails_triggered=guardrails,
            candidate_evaluations=evals,
            counterfactuals=counterfactuals,
            model_version=self.model_version,
            timestamp=datetime.now(timezone.utc),
        )
