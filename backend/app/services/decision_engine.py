"""
RecoverOS Decision Engine.

Orchestrates candidate generation, ML probability inference using the frozen
Phase-1 model artifact, Expected Recovery Value calculation, and guardrail enforcement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import joblib
import pandas as pd

from backend.app.core.config import (
    ACTIONS,
    ACTION_COSTS,
    MODEL_ARTIFACT_PATH,
    MODEL_VERSION,
)
from backend.app.schemas.recovery import (
    ActionCandidateEvaluation,
    DiagnosisRequest,
    DiagnosisResponse,
)
from backend.app.services.guardrails import GuardrailEngine

# Ensure feature transformer is in namespace for joblib unpickling
from ml.transformers import FeatureEngineeringTransformer

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Core decision engine responsible for scoring candidate recovery actions
    and applying deterministic guardrails.
    """

    def __init__(
        self,
        model_artifact_path: Optional[Path] = MODEL_ARTIFACT_PATH,
        model: Optional[Any] = None,
        guardrail_engine: Optional[GuardrailEngine] = None,
    ):
        self.model_path = model_artifact_path
        self.model = model
        self.guardrail_engine = guardrail_engine or GuardrailEngine()
        self.model_version = MODEL_VERSION

        if self.model is None and self.model_path is not None:
            self._load_model()

    def _load_model(self) -> None:
        """Loads the Phase-1 trained model artifact using joblib."""
        try:
            if self.model_path and self.model_path.exists():
                self.model = joblib.load(self.model_path)
                logger.info(f"Successfully loaded model artifact from {self.model_path}")
            else:
                logger.warning(
                    f"Model artifact not found at {self.model_path}. Running in degraded fallback mode."
                )
                self.model = None
        except Exception as e:
            logger.error(f"Failed to load model artifact from {self.model_path}: {e}")
            self.model = None

    def _build_candidate_dataframe(self, request: DiagnosisRequest) -> pd.DataFrame:
        """Constructs a DataFrame containing 1 row per candidate action for model scoring."""
        base_dict = request.model_dump()
        rows = []
        for action in ACTIONS:
            row = dict(base_dict)
            row["action"] = action
            rows.append(row)
        return pd.DataFrame(rows)

    def evaluate_request(
        self,
        request: DiagnosisRequest,
    ) -> Tuple[List[ActionCandidateEvaluation], str, str, str, List[str]]:
        """
        Executes the full evaluation pipeline:
        1. Model probability scoring across all candidate actions.
        2. ERV calculation: amount * P_pred - cost.
        3. Deterministic guardrail evaluation.
        4. Optimal permitted action selection.

        Returns
        -------
        Tuple[List[ActionCandidateEvaluation], str, str, str, List[str]]
            (candidate_evaluations, selected_action, decision_status, decision_reason, guardrails_triggered)
        """
        # Safe Fallback Check: If model is unavailable, return safe non-destructive fallback
        if self.model is None:
            return self.guardrail_engine.get_degraded_state_fallback(
                request, "Model artifact is unavailable."
            )

        try:
            candidate_df = self._build_candidate_dataframe(request)
            pred_probs = self.model.predict_proba(candidate_df)[:, 1]

            amount = float(request.amount)
            unfiltered_evaluations: Dict[str, Tuple[float, float, float]] = {}

            for idx, action in enumerate(ACTIONS):
                if action == "stop":
                    p = 0.0
                    cost = 0.0
                    erv = 0.0
                else:
                    p = float(pred_probs[idx])
                    cost = float(ACTION_COSTS[action])
                    erv = float((amount * p) - cost)

                unfiltered_evaluations[action] = (p, cost, erv)

            # Evaluate candidate actions against deterministic guardrails
            evaluations, triggered_guardrails = self.guardrail_engine.evaluate_candidates(
                request, unfiltered_evaluations
            )

            # Select best permitted action
            selected_action, status, reason = self.guardrail_engine.select_best_action(
                evaluations
            )

            return evaluations, selected_action, status, reason, triggered_guardrails

        except Exception as e:
            logger.error(f"Inference error during decision pipeline: {e}")
            return self.guardrail_engine.get_degraded_state_fallback(
                request, f"Inference exception: {str(e)}"
            )

    def diagnose(self, request: DiagnosisRequest) -> DiagnosisResponse:
        """
        Performs diagnosis and returns candidate scoring breakdown without ledger persistence.
        """
        evals, action, status, reason, guardrails = self.evaluate_request(request)
        return DiagnosisResponse(
            transaction_id=request.transaction_id,
            amount=request.amount,
            recommended_action=action,
            decision_status=status,
            decision_reason=reason,
            guardrails_triggered=guardrails,
            candidate_evaluations=evals,
            model_version=self.model_version,
            timestamp=datetime.now(timezone.utc),
        )
