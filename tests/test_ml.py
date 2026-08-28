"""
Tests for RecoverOS ML Pipeline, Feature Transformers, and Evaluation.
"""

import pytest
import numpy as np
import pandas as pd
from pathlib import Path

from ml.train import build_pipeline, train_model, save_model_artifact, load_model_artifact
from ml.transformers import FeatureEngineeringTransformer
from ml.evaluate import compute_expected_calibration_error, evaluate_ml_model


@pytest.fixture
def sample_dataset() -> pd.DataFrame:
    from ml.synthetic_generator import generate_synthetic_dataset
    return generate_synthetic_dataset(n_records=300, seed=99)


def test_feature_engineering_transformer(sample_dataset):
    """Verify FeatureEngineeringTransformer adds required interaction columns."""
    transformer = FeatureEngineeringTransformer()
    transformed = transformer.transform(sample_dataset)

    assert "failure_action_interaction" in transformed.columns
    assert "method_action_interaction" in transformed.columns
    assert "previous_success_rate" in transformed.columns
    assert "previous_recovery_rate" in transformed.columns


def test_model_training_and_evaluation(sample_dataset):
    """Verify end-to-end model training, probability output, and metrics."""
    train_df = sample_dataset.iloc[:200].reset_index(drop=True)
    val_df = sample_dataset.iloc[200:].reset_index(drop=True)

    pipeline, metrics = train_model(train_df, val_df)

    assert "val_roc_auc" in metrics
    assert "val_pr_auc" in metrics
    assert "val_log_loss" in metrics
    assert "val_brier_score" in metrics

    probs = pipeline.predict_proba(val_df)
    assert probs.shape == (len(val_df), 2)
    assert np.allclose(probs.sum(axis=1), 1.0)
    assert (probs >= 0.0).all() and (probs <= 1.0).all()


def test_model_artifact_save_load(sample_dataset, tmp_path):
    """Verify model persistence round-trip using joblib."""
    train_df = sample_dataset.iloc[:200].reset_index(drop=True)
    val_df = sample_dataset.iloc[200:].reset_index(drop=True)

    pipeline, _ = train_model(train_df, val_df)
    temp_model_path = tmp_path / "test_model.joblib"

    save_model_artifact(pipeline, temp_model_path)
    loaded_pipeline = load_model_artifact(temp_model_path)

    preds_orig = pipeline.predict_proba(val_df)
    preds_loaded = loaded_pipeline.predict_proba(val_df)

    assert np.allclose(preds_orig, preds_loaded)


def test_expected_calibration_error_calculation():
    """Verify ECE calculation for perfect and imperfect calibrations."""
    # Perfect calibration: probs match labels perfectly
    y_true = np.array([1, 1, 0, 0])
    y_prob = np.array([1.0, 1.0, 0.0, 0.0])
    ece = compute_expected_calibration_error(y_true, y_prob, n_bins=5)
    assert ece == 0.0
