"""
RecoverOS Feature Engineering Transformers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class FeatureEngineeringTransformer(BaseEstimator, TransformerMixin):
    """
    Constructs derived rates and categorical interaction features:
    - failure_action_interaction: failure_type + '__' + action
    - method_action_interaction: payment_method + '__' + action
    - previous_success_rate: previous_success_count / previous_payment_count
    - previous_recovery_rate: previous_recovery_count / previous_failure_count
    """

    def fit(self, X: pd.DataFrame, y: np.ndarray = None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X_out = X.copy()

        # Compute rates if not already present
        if "previous_success_rate" not in X_out.columns:
            prev_pay = np.maximum(1, X_out.get("previous_payment_count", 1))
            X_out["previous_success_rate"] = X_out.get("previous_success_count", 0) / prev_pay

        if "previous_recovery_rate" not in X_out.columns:
            prev_fail = np.maximum(1, X_out.get("previous_failure_count", 0))
            X_out["previous_recovery_rate"] = X_out.get("previous_recovery_count", 0) / prev_fail

        # Categorical interaction terms for action-specific dynamics
        f_type = X_out["failure_type"].astype(str) if "failure_type" in X_out.columns else "unknown"
        act = X_out["action"].astype(str) if "action" in X_out.columns else "stop"
        p_meth = X_out["payment_method"].astype(str) if "payment_method" in X_out.columns else "card"

        X_out["failure_action_interaction"] = f_type + "__" + act
        X_out["method_action_interaction"] = p_meth + "__" + act

        return X_out
