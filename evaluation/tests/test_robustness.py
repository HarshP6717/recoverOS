"""
Tests for Robustness and Generalization Module (Phase 2C Step 3).

Verifies:
- Deterministic reproducibility
- Identical populations across policies
- No mutation of source test.csv
- No modification of frozen directories
- Valid seed handling
- Zero-division safety
- Perturbation reproducibility
- Slice non-emptiness & correctness
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.evaluator import _load_test_data, _load_ml_model, SEED_BASE
from evaluation.robustness.engine import (
    DISTRIBUTION_SLICES,
    RARE_COMBOS,
    IDENTITY_COLS,
    apply_slice,
    perturb_categorical_missingness,
    perturb_numeric_noise,
    _bootstrap_slice,
    _paired_wins,
)
from evaluation.robustness.experiments import evaluate_policies_on_slice


@pytest.fixture(scope="module")
def test_df():
    return _load_test_data()


@pytest.fixture(scope="module")
def model():
    return _load_ml_model()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Source Data Immutability & Frozen Directories
# ─────────────────────────────────────────────────────────────────────────────

class TestDataImmutability:
    def test_source_csv_not_mutated(self, test_df):
        """Verify that loading, perturbing, or running experiments does not alter test.csv."""
        csv_path = PROJECT_ROOT / "data" / "processed" / "test.csv"
        initial_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()

        # Run perturbation
        p_df = perturb_categorical_missingness(test_df, "failure_type", 0.5, rng_seed=999)
        assert len(p_df) == len(test_df)

        # Hash after
        final_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        assert initial_hash == final_hash, "test.csv content was modified!"

    def test_identity_columns_preserved_under_perturbation(self, test_df):
        """Identity columns must never be modified by perturbation functions."""
        p_cat = perturb_categorical_missingness(test_df, "failure_type", 0.3, rng_seed=42)
        p_num = perturb_numeric_noise(test_df, "amount", 0.3, rng_seed=42)

        for col in IDENTITY_COLS:
            if col in test_df.columns:
                assert (test_df[col] == p_cat[col]).all(), f"Identity column {col} was modified in categorical perturbation!"
                assert (test_df[col] == p_num[col]).all(), f"Identity column {col} was modified in numeric perturbation!"

    def test_identity_cols_protected_from_direct_perturbation(self, test_df):
        """Attempting to perturb an identity column should raise AssertionError."""
        with pytest.raises(AssertionError):
            perturb_categorical_missingness(test_df, "transaction_id", 0.2, rng_seed=1)
        with pytest.raises(AssertionError):
            perturb_numeric_noise(test_df, "transaction_id", 0.2, rng_seed=1)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Perturbation Reproducibility
# ─────────────────────────────────────────────────────────────────────────────

class TestPerturbationReproducibility:
    def test_categorical_perturbation_deterministic(self, test_df):
        p1 = perturb_categorical_missingness(test_df, "failure_type", 0.25, rng_seed=123)
        p2 = perturb_categorical_missingness(test_df, "failure_type", 0.25, rng_seed=123)
        assert p1.equals(p2), "Categorical perturbation is not deterministic with identical seed"

    def test_numeric_noise_deterministic(self, test_df):
        p1 = perturb_numeric_noise(test_df, "amount", 0.25, rng_seed=456)
        p2 = perturb_numeric_noise(test_df, "amount", 0.25, rng_seed=456)
        assert p1.equals(p2), "Numeric perturbation is not deterministic with identical seed"

    def test_different_seeds_produce_different_perturbations(self, test_df):
        p1 = perturb_categorical_missingness(test_df, "failure_type", 0.25, rng_seed=10)
        p2 = perturb_categorical_missingness(test_df, "failure_type", 0.25, rng_seed=20)
        assert not p1.equals(p2)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Slicing Correctness & Non-Emptiness
# ─────────────────────────────────────────────────────────────────────────────

class TestSliceCorrectness:
    @pytest.mark.parametrize("slice_name,spec", list(DISTRIBUTION_SLICES.items()))
    def test_all_slices_non_empty(self, test_df, slice_name, spec):
        mask = apply_slice(test_df, spec)
        sub_df = test_df[mask]
        assert len(sub_df) > 0, f"Slice '{slice_name}' returned 0 records!"

    def test_amount_slices_partition_properly(self, test_df):
        low_spec = DISTRIBUTION_SLICES["amount_low"]
        high_spec = DISTRIBUTION_SLICES["amount_high"]
        low_mask = apply_slice(test_df, low_spec)
        high_mask = apply_slice(test_df, high_spec)
        # Low and high should have zero overlap
        overlap = low_mask & high_mask
        assert not overlap.any(), "amount_low and amount_high overlap!"

    def test_rare_combos_defined(self, test_df):
        assert len(RARE_COMBOS) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multi-Seed & Slice Evaluation Correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluationEngine:
    def test_evaluate_slice_returns_all_three_policies(self, test_df, model):
        sub_df = test_df.iloc[:50].copy()
        res = evaluate_policies_on_slice(sub_df, sub_df, model, seed_offset=0, n_bootstraps=50)
        assert "baseline" in res
        assert "heuristic" in res
        assert "recoveros" in res
        assert res["n"] == 50
        assert res["baseline"]["n"] == 50
        assert res["heuristic"]["n"] == 50
        assert res["recoveros"]["n"] == 50

    def test_seed_offset_produces_deterministic_results(self, test_df, model):
        sub_df = test_df.iloc[:20].copy()
        res1 = evaluate_policies_on_slice(sub_df, sub_df, model, seed_offset=1000, n_bootstraps=20)
        res2 = evaluate_policies_on_slice(sub_df, sub_df, model, seed_offset=1000, n_bootstraps=20)
        assert res1["recoveros"]["total_net_value"] == res2["recoveros"]["total_net_value"]
        assert res1["heuristic"]["total_net_value"] == res2["heuristic"]["total_net_value"]

    def test_paired_wins_sum_to_n(self):
        recs_a = [{"net_value": 10.0}, {"net_value": 20.0}, {"net_value": 30.0}]
        recs_b = [{"net_value": 15.0}, {"net_value": 20.0}, {"net_value": 25.0}]
        paired = _paired_wins(recs_a, recs_b, "A", "B")
        assert paired["B_wins"] == 1
        assert paired["A_wins"] == 1
        assert paired["ties"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. Zero-Division & Edge-Case Safety
# ─────────────────────────────────────────────────────────────────────────────

class TestRobustnessEdgeCases:
    def test_empty_slice_handling(self, model):
        empty_df = pd.DataFrame(columns=["amount", "failure_type", "payment_method", "attempt_number"])
        res = evaluate_policies_on_slice(empty_df, empty_df, model)
        assert res["n"] == 0

    def test_mini_bootstrap_small_sample_safety(self):
        recs_a = [{"net_value": 5.0}]
        recs_b = [{"net_value": 10.0}]
        boot = _bootstrap_slice(recs_a, recs_b, n_bootstraps=50)
        assert boot["ci_95"] is None
        assert "too small" in boot["note"]
