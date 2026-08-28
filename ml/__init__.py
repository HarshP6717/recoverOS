"""
RecoverOS Machine Learning Package.

Modules:
- synthetic_generator: Generates 10,000 reproducible synthetic subscription payment failures.
- train: Prepares features, trains interpretable Logistic Regression model, and persists artifact.
- evaluate: Computes ML metrics (ROC-AUC, PR-AUC, Log Loss, Brier, Calibration) and policy simulation comparisons.
"""

from ml.synthetic_generator import generate_synthetic_dataset, save_dataset_splits

__all__ = [
    "generate_synthetic_dataset",
    "save_dataset_splits",
]
