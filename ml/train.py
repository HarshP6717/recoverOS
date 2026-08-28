"""
RecoverOS Model Training Pipeline.

Trains an interpretable, calibrated Logistic Regression model to predict
action-specific recovery probability P(recovered = 1 | features, action).

The trained pipeline is validated on the validation split and persisted to
ml/models/recovery_model.joblib using joblib.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from typing import Dict, List, Tuple
import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.transformers import FeatureEngineeringTransformer

DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_DIR = PROJECT_ROOT / "ml" / "models"

# Feature definitions
NUMERICAL_FEATURES: List[str] = [
    "amount",
    "attempt_number",
    "days_overdue",
    "previous_payment_count",
    "previous_success_count",
    "previous_failure_count",
    "previous_recovery_count",
    "customer_lifetime_value",
    "contact_count",
    "subscription_age_days",
    "previous_success_rate",
    "previous_recovery_rate",
]

CATEGORICAL_FEATURES: List[str] = [
    "payment_method",
    "failure_type",
    "action",
    "failure_action_interaction",
    "method_action_interaction",
]


def build_pipeline() -> Pipeline:
    """
    Builds the Scikit-Learn training pipeline with preprocessing and calibrated Logistic Regression.
    """
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "num",
                StandardScaler(),
                NUMERICAL_FEATURES,
            ),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )

    base_logreg = LogisticRegression(
        C=1.0,
        max_iter=1000,
        random_state=42,
        class_weight=None,
        solver="lbfgs",
    )

    # Sigmoid calibration over logistic regression to ensure well-calibrated probabilities
    calibrated_clf = CalibratedClassifierCV(
        estimator=base_logreg,
        method="sigmoid",
        cv=5,
    )

    pipeline = Pipeline(
        steps=[
            ("feature_engineer", FeatureEngineeringTransformer()),
            ("preprocessor", preprocessor),
            ("classifier", calibrated_clf),
        ]
    )

    return pipeline


def train_model(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
) -> Tuple[Pipeline, Dict[str, float]]:
    """
    Trains the ML pipeline on train_df and evaluates on val_df.

    Parameters
    ----------
    train_df : pd.DataFrame
        Training records (8,000 rows).
    val_df : pd.DataFrame
        Validation records (1,000 rows).

    Returns
    -------
    Tuple[Pipeline, Dict[str, float]]
        Fitted pipeline and validation metrics dictionary.
    """
    y_train = train_df["recovered"].astype(int).values
    y_val = val_df["recovered"].astype(int).values

    pipeline = build_pipeline()
    pipeline.fit(train_df, y_train)

    # Evaluate on validation split
    val_probs = pipeline.predict_proba(val_df)[:, 1]

    roc_auc = float(roc_auc_score(y_val, val_probs))
    pr_auc = float(average_precision_score(y_val, val_probs))
    ll = float(log_loss(y_val, val_probs))
    brier = float(brier_score_loss(y_val, val_probs))

    val_metrics = {
        "val_roc_auc": round(roc_auc, 4),
        "val_pr_auc": round(pr_auc, 4),
        "val_log_loss": round(ll, 4),
        "val_brier_score": round(brier, 4),
    }

    return pipeline, val_metrics


def save_model_artifact(pipeline: Pipeline, output_path: Path = MODEL_DIR / "recovery_model.joblib") -> Path:
    """Serializes the fitted pipeline with joblib."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, output_path)
    print(f"Model saved to: {output_path}")
    return output_path


def load_model_artifact(model_path: Path = MODEL_DIR / "recovery_model.joblib") -> Pipeline:
    """Loads a serialized model artifact."""
    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found at {model_path}. Train the model first.")
    return joblib.load(model_path)


if __name__ == "__main__":
    print("Loading processed datasets...")
    train_df = pd.read_csv(DATA_PROCESSED_DIR / "train.csv")
    val_df = pd.read_csv(DATA_PROCESSED_DIR / "val.csv")

    print(f"Training on {len(train_df)} records, validating on {len(val_df)} records...")
    pipeline, val_metrics = train_model(train_df, val_df)

    print("\n--- Validation Split Metrics ---")
    for metric_name, value in val_metrics.items():
        print(f"  {metric_name}: {value}")

    save_model_artifact(pipeline)
