"""
RecoverOS Policy Implementations.

Defines recovery action selection strategies:
1. DeterministicBaselinePolicy: Fixed heuristic dunning rules.
2. GroundTruthOraclePolicy: "Ground-Truth Oracle — theoretical upper bound"
   (Uses unobservable ground-truth probabilities; unavailable to production).
3. MLExpectedValuePolicy: RecoverOS ML policy using predicted recovery probabilities,
   expected recovery values (ERV in INR ₹), and deterministic guardrails.
"""

from __future__ import annotations

import math
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.recovery_simulator import (
    ACTIONS,
    ACTION_COSTS,
    compute_expected_recovery_value,
    compute_ground_truth_recovery_probability,
    evaluate_all_actions_ground_truth,
)


class BasePolicy(ABC):
    """Abstract base class for payment recovery policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable policy name."""
        pass

    @abstractmethod
    def select_action(self, record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]]) -> str:
        """
        Selects a recovery action for a single payment failure record.

        Parameters
        ----------
        record : Union[Dict[str, Any], pd.Series, Mapping[str, Any]]
            Payment failure record containing features.

        Returns
        -------
        str
            Chosen recovery action from ACTIONS.
        """
        pass

    def select_actions_batch(self, df: pd.DataFrame) -> List[str]:
        """
        Selects recovery actions for a batch of payment records.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame of payment failure records.

        Returns
        -------
        List[str]
            List of chosen actions.
        """
        return [self.select_action(row) for _, row in df.iterrows()]


class DeterministicBaselinePolicy(BasePolicy):
    """
    Deterministic rule-based baseline dunning strategy.

    Uses fixed heuristics based on attempt number and failure type.
    Does not use machine learning or probability estimates.

    Rules:
    - Attempt 1:
      - If hard failure ('expired_card', 'hard_decline', 'invalid_payment_method') -> 'payment_method_update'
      - Else -> 'retry_now'
    - Attempt 2:
      - If hard failure -> 'recovery_link'
      - Else -> 'retry_later'
    - Attempt 3:
      - 'send_reminder'
    - Attempt 4+:
      - 'stop'
    """

    @property
    def name(self) -> str:
        return "Deterministic Baseline"

    def select_action(self, record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]]) -> str:
        attempt_num = int(record.get("attempt_number", 1))
        failure_type = str(record.get("failure_type", "unknown"))

        hard_failures = {"expired_card", "hard_decline", "invalid_payment_method"}

        if attempt_num <= 1:
            if failure_type in hard_failures:
                return "payment_method_update"
            return "retry_now"
        elif attempt_num == 2:
            if failure_type in hard_failures:
                return "recovery_link"
            return "retry_later"
        elif attempt_num == 3:
            return "send_reminder"
        else:
            return "stop"


class GroundTruthOraclePolicy(BasePolicy):
    """
    Ground-Truth Oracle — theoretical upper bound.

    NOTE: This policy has direct access to unobservable ground-truth recovery
    probabilities and serves strictly as the theoretical upper bound ceiling.
    It is NOT available to the production system.
    """

    @property
    def name(self) -> str:
        return "Ground-Truth Oracle (Theoretical Upper Bound)"

    def select_action(self, record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]]) -> str:
        all_ground_truth = evaluate_all_actions_ground_truth(record)

        best_action = "stop"
        max_erv = 0.0  # Stop has ERV = 0.0

        for action, stats in all_ground_truth.items():
            erv = stats["expected_recovery_value"]
            if erv > max_erv:
                max_erv = erv
                best_action = action

        return best_action


class MLExpectedValuePolicy(BasePolicy):
    """
    RecoverOS ML Expected Value Policy with Deterministic Guardrails.

    Workflow:
    1. For every candidate action in ACTIONS, predicts recovery probability
       P(recovery | features, action) using the trained ML model pipeline.
    2. Computes predicted ERV = amount * P_pred - action_cost (in INR ₹).
    3. Enforces deterministic safety guardrails:
       - Guardrail 1 (Negative ROI suppression): If max ERV <= 0, chooses 'stop'.
       - Guardrail 2 (Permanent failure suppression): Suppresses 'retry_now' and 'retry_later'
         when failure_type is 'hard_decline', 'expired_card', or 'invalid_payment_method'.
       - Guardrail 3 (Micro-amount cost guardrail): Suppresses 'escalate_human' (₹30.00 cost)
         if invoice amount < ₹100.00 to avoid high margin erosion.
    4. Selects the permitted action with the highest predicted ERV.
    """

    def __init__(self, model_pipeline: Any, guardrails_enabled: bool = True):
        """
        Parameters
        ----------
        model_pipeline : Any
            Trained Scikit-Learn estimator/pipeline that implements predict_proba(X).
        guardrails_enabled : bool
            Whether deterministic safety guardrails are active (default: True).
        """
        self.model = model_pipeline
        self.guardrails_enabled = guardrails_enabled

    @property
    def name(self) -> str:
        return "RecoverOS ML Policy (Expected Value + Guardrails)"

    def _get_candidate_df(
        self,
        record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]],
    ) -> pd.DataFrame:
        """Constructs a DataFrame with one row per candidate action for prediction."""
        rows = []
        for action in ACTIONS:
            r = dict(record)
            r["action"] = action
            rows.append(r)
        return pd.DataFrame(rows)

    def select_action(self, record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]]) -> str:
        amount = float(record.get("amount", 0.0))
        failure_type = str(record.get("failure_type", "unknown"))

        # Build candidate DataFrame for all 7 actions
        candidate_df = self._get_candidate_df(record)

        # Predict recovery probabilities for all actions
        # For 'stop', probability is mathematically 0.0
        probabilities: Dict[str, float] = {}
        stop_idx = ACTIONS.index("stop")

        # Get ML model predicted probabilities (index 1 is positive class)
        model_probs = self.model.predict_proba(candidate_df)[:, 1]

        for i, action in enumerate(ACTIONS):
            if action == "stop":
                probabilities[action] = 0.0
            else:
                probabilities[action] = float(model_probs[i])

        # Compute predicted ERV for each candidate action
        predicted_ervs: Dict[str, float] = {}
        for action in ACTIONS:
            p = probabilities[action]
            predicted_ervs[action] = compute_expected_recovery_value(amount, p, action)

        # Determine permitted actions according to deterministic guardrails
        permitted_actions = set(ACTIONS)

        if self.guardrails_enabled:
            # Guardrail 2: Suppress automated retries on permanent failures
            if failure_type in {"hard_decline", "expired_card", "invalid_payment_method"}:
                permitted_actions.discard("retry_now")
                permitted_actions.discard("retry_later")

            # Guardrail 3: Suppress human escalation (₹30.00) on micro-invoices (< ₹100.00)
            if amount < 100.0:
                permitted_actions.discard("escalate_human")

        # Find permitted action with highest predicted ERV
        best_action = "stop"
        best_erv = 0.0  # Stop has 0.0 ERV (Guardrail 1: do not act if ERV <= 0)

        for action in ACTIONS:
            if action in permitted_actions:
                erv = predicted_ervs[action]
                if erv > best_erv:
                    best_erv = erv
                    best_action = action

        return best_action

    def select_actions_batch(self, df: pd.DataFrame) -> List[str]:
        """
        Vectorized batch action selection for all records in df.
        Evaluates all candidate actions per record and selects the highest ERV.
        """
        n_records = len(df)
        if n_records == 0:
            return []

        # Expand df to n_records * len(ACTIONS) rows
        expanded_dfs = []
        for action in ACTIONS:
            action_df = df.copy()
            action_df["action"] = action
            expanded_dfs.append(action_df)

        # Stack so records are grouped or ordered
        # We'll predict in batch
        full_candidate_df = pd.concat(expanded_dfs, ignore_index=True)
        all_pred_probs = self.model.predict_proba(full_candidate_df)[:, 1]

        # Reshape to (len(ACTIONS), n_records) then transpose to (n_records, len(ACTIONS))
        n_actions = len(ACTIONS)
        prob_matrix = all_pred_probs.reshape((n_actions, n_records)).T

        # Force 'stop' probability to 0.0
        stop_idx = ACTIONS.index("stop")
        prob_matrix[:, stop_idx] = 0.0

        chosen_actions = []
        amounts = df["amount"].values
        failure_types = df["failure_type"].values if "failure_type" in df.columns else ["unknown"] * n_records

        hard_failures = {"hard_decline", "expired_card", "invalid_payment_method"}
        retry_now_idx = ACTIONS.index("retry_now")
        retry_later_idx = ACTIONS.index("retry_later")
        escalate_idx = ACTIONS.index("escalate_human")

        cost_array = np.array([ACTION_COSTS[a] for a in ACTIONS])

        for i in range(n_records):
            amt = float(amounts[i])
            ftype = str(failure_types[i])
            p_vec = prob_matrix[i]
            erv_vec = (amt * p_vec) - cost_array

            # Apply guardrails
            if self.guardrails_enabled:
                if ftype in hard_failures:
                    erv_vec[retry_now_idx] = -1e9
                    erv_vec[retry_later_idx] = -1e9
                if amt < 100.0:
                    erv_vec[escalate_idx] = -1e9

            # Best action index
            max_idx = int(np.argmax(erv_vec))
            if erv_vec[max_idx] <= 0.0:
                chosen_actions.append("stop")
            else:
                chosen_actions.append(ACTIONS[max_idx])

        return chosen_actions
