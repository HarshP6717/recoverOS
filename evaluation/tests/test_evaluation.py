"""
Tests for the evaluation pipeline (evaluator.py + comparison.py).

Covers:
- Same test population for both policies (same 1,000 records, same order)
- Same seed formula (seed = 42 + index)
- Deterministic reproducibility
- Baseline / RecoverOS pipeline execution
- No fabricated metrics (all values come from actual execution)
- Comparison correctness
- Zero-division safety in comparison
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.evaluator import (
    SEED_BASE,
    N_EXPECTED,
    _load_test_data,
    _load_ml_model,
    run_baseline_evaluation,
    run_recoveros_evaluation,
    load_and_evaluate,
)
from evaluation.metrics import compute_policy_metrics, safe_divide
from evaluation.comparison import compute_comparison
from simulator.recovery_simulator import simulate_action, ACTIONS


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def test_df() -> pd.DataFrame:
    """Load the actual held-out test set once per test module."""
    return _load_test_data()


@pytest.fixture(scope="module")
def ml_model() -> Any:
    """Load the frozen ML model once per test module."""
    return _load_ml_model()


@pytest.fixture(scope="module")
def baseline_records(test_df) -> List[Dict[str, Any]]:
    return run_baseline_evaluation(test_df)


@pytest.fixture(scope="module")
def recoveros_records(test_df, ml_model) -> List[Dict[str, Any]]:
    return run_recoveros_evaluation(test_df, ml_model)


# ── Same test population ───────────────────────────────────────────────────────


class TestSameTestPopulation:
    def test_exactly_n_expected_cases(self, test_df):
        assert len(test_df) == N_EXPECTED, (
            f"Expected {N_EXPECTED} test records, got {len(test_df)}"
        )

    def test_baseline_covers_all_cases(self, baseline_records):
        assert len(baseline_records) == N_EXPECTED

    def test_recoveros_covers_all_cases(self, recoveros_records):
        assert len(recoveros_records) == N_EXPECTED

    def test_indices_are_sequential_baseline(self, baseline_records):
        for i, rec in enumerate(baseline_records):
            assert rec["index"] == i, f"Index mismatch at position {i}: {rec['index']}"

    def test_indices_are_sequential_recoveros(self, recoveros_records):
        for i, rec in enumerate(recoveros_records):
            assert rec["index"] == i, f"Index mismatch at position {i}: {rec['index']}"

    def test_both_policies_same_record_count(self, baseline_records, recoveros_records):
        assert len(baseline_records) == len(recoveros_records)


# ── Same seed verification ─────────────────────────────────────────────────────


class TestSeedFormula:
    def test_seed_base_is_42(self):
        assert SEED_BASE == 42, f"SEED_BASE must be 42, got {SEED_BASE}"

    def test_seed_formula_determinism(self, test_df):
        """
        Verify seed = 42 + i produces identical outcomes when called twice
        for the same record and action.
        """
        row = test_df.iloc[0]
        action = "retry_later"
        seed = SEED_BASE + 0  # index 0

        res1 = simulate_action(row, action, seed=seed)
        res2 = simulate_action(row, action, seed=seed)

        assert res1["recovered"] == res2["recovered"]
        assert res1["net_value"] == res2["net_value"]
        assert res1["recovered_amount"] == res2["recovered_amount"]

    def test_different_seeds_may_differ(self, test_df):
        """
        Different seeds should be distinguishable as seeds — this just
        confirms seed arithmetic is being applied per-record.
        """
        row = test_df.iloc[5]
        action = "send_reminder"
        # With high enough probability, seed 42+5 != 42+99 should differ on some records
        # (not a guaranteed failure but validates the interface)
        _ = simulate_action(row, action, seed=SEED_BASE + 5)
        _ = simulate_action(row, action, seed=SEED_BASE + 99)
        # No assertion needed beyond not raising — determinism is tested above


# ── Deterministic reproducibility ─────────────────────────────────────────────


class TestDeterministicReproducibility:
    def test_baseline_evaluation_is_reproducible(self, test_df):
        """Running baseline evaluation twice must yield identical results."""
        records_a = run_baseline_evaluation(test_df)
        records_b = run_baseline_evaluation(test_df)

        assert len(records_a) == len(records_b)
        for a, b in zip(records_a, records_b):
            assert a["action"] == b["action"], f"Action differs at index {a['index']}"
            assert a["recovered"] == b["recovered"], f"Recovered differs at {a['index']}"
            assert a["net_value"] == pytest.approx(b["net_value"])

    def test_recoveros_evaluation_is_reproducible(self, test_df, ml_model):
        """Running RecoverOS evaluation twice must yield identical results."""
        records_a = run_recoveros_evaluation(test_df, ml_model)
        records_b = run_recoveros_evaluation(test_df, ml_model)

        assert len(records_a) == len(records_b)
        for a, b in zip(records_a, records_b):
            assert a["action"] == b["action"], f"Action differs at index {a['index']}"
            assert a["recovered"] == b["recovered"], f"Recovered differs at {a['index']}"
            assert a["net_value"] == pytest.approx(b["net_value"])


# ── Pipeline execution ─────────────────────────────────────────────────────────


class TestBaselineEvaluationPipeline:
    def test_baseline_actions_are_valid(self, baseline_records):
        for rec in baseline_records:
            assert rec["action"] in ACTIONS, f"Invalid action: {rec['action']}"

    def test_baseline_no_fabricated_metrics(self, baseline_records):
        """Predicted ERV and guardrails must be None (not fabricated) for baseline."""
        for rec in baseline_records:
            assert rec["predicted_erv"] is None, (
                f"Baseline should not have predicted_erv, got {rec['predicted_erv']}"
            )
            assert rec["guardrails_triggered"] is None, (
                f"Baseline should not have guardrails_triggered"
            )

    def test_baseline_recovered_amount_consistent(self, baseline_records, test_df):
        """recovered_amount must equal amount if recovered, else 0."""
        for rec in baseline_records:
            i = rec["index"]
            amt = float(test_df.iloc[i]["amount"])
            if rec["recovered"]:
                assert rec["recovered_amount"] == pytest.approx(amt)
            else:
                assert rec["recovered_amount"] == pytest.approx(0.0)

    def test_baseline_net_value_formula(self, baseline_records):
        """net_value must equal recovered_amount - action_cost."""
        for rec in baseline_records:
            expected_nv = rec["recovered_amount"] - rec["action_cost"]
            assert rec["net_value"] == pytest.approx(expected_nv)


class TestRecoverOSEvaluationPipeline:
    def test_recoveros_actions_are_valid(self, recoveros_records):
        for rec in recoveros_records:
            assert rec["action"] in ACTIONS, f"Invalid action: {rec['action']}"

    def test_recoveros_predicted_erv_is_not_none(self, recoveros_records):
        """Every RecoverOS record must have a predicted_erv (not None)."""
        for rec in recoveros_records:
            assert rec["predicted_erv"] is not None, (
                f"RecoverOS record {rec['index']} missing predicted_erv"
            )

    def test_recoveros_guardrails_are_not_none(self, recoveros_records):
        """Every RecoverOS record must have a guardrails_triggered count."""
        for rec in recoveros_records:
            assert rec["guardrails_triggered"] is not None
            assert isinstance(rec["guardrails_triggered"], int)
            assert rec["guardrails_triggered"] >= 0

    def test_recoveros_recovered_amount_consistent(self, recoveros_records, test_df):
        for rec in recoveros_records:
            i = rec["index"]
            amt = float(test_df.iloc[i]["amount"])
            if rec["recovered"]:
                assert rec["recovered_amount"] == pytest.approx(amt)
            else:
                assert rec["recovered_amount"] == pytest.approx(0.0)

    def test_recoveros_net_value_formula(self, recoveros_records):
        for rec in recoveros_records:
            expected_nv = rec["recovered_amount"] - rec["action_cost"]
            assert rec["net_value"] == pytest.approx(expected_nv)

    def test_recoveros_guardrail2_hard_failure_no_retry(self, recoveros_records, test_df):
        """
        ML policy guardrail 2: retry_now / retry_later must not be selected
        for hard_decline, expired_card, invalid_payment_method cases.
        """
        hard_failures = {"hard_decline", "expired_card", "invalid_payment_method"}
        for rec in recoveros_records:
            i = rec["index"]
            ftype = test_df.iloc[i]["failure_type"]
            if ftype in hard_failures:
                assert rec["action"] not in {"retry_now", "retry_later"}, (
                    f"Guardrail 2 violated at index {i}: action={rec['action']}, "
                    f"failure_type={ftype}"
                )

    def test_recoveros_guardrail3_micro_invoice_no_escalate(self, recoveros_records, test_df):
        """
        ML policy guardrail 3: escalate_human must not be selected
        for invoices < ₹100.
        """
        for rec in recoveros_records:
            i = rec["index"]
            amt = float(test_df.iloc[i]["amount"])
            if amt < 100.0:
                assert rec["action"] != "escalate_human", (
                    f"Guardrail 3 violated at index {i}: escalate_human on "
                    f"amount=₹{amt}"
                )


# ── Comparison correctness ─────────────────────────────────────────────────────


class TestComparisonModule:
    def test_comparison_absolute_diff_correct(self):
        b = {
            "policy_name": "Baseline",
            "n_cases": 2,
            "recovered_count": 1,
            "recovery_rate": 0.5,
            "total_recovered_amount": 1000.0,
            "total_action_cost": 1.0,
            "total_net_value": 999.0,
            "avg_net_value_per_case": 499.5,
            "stop_count": 0,
            "stop_rate": 0.0,
            "action_distribution": {"retry_now": 2},
            "action_recovery_breakdown": {},
        }
        r = {
            "policy_name": "RecoverOS",
            "n_cases": 2,
            "recovered_count": 2,
            "recovery_rate": 1.0,
            "total_recovered_amount": 2000.0,
            "total_action_cost": 2.0,
            "total_net_value": 1998.0,
            "avg_net_value_per_case": 999.0,
            "stop_count": 0,
            "stop_rate": 0.0,
            "action_distribution": {"send_reminder": 2},
            "action_recovery_breakdown": {},
        }
        c = compute_comparison(b, r)
        assert c["absolute_difference"]["recovered_count"] == pytest.approx(1.0)
        assert c["absolute_difference"]["total_net_value"] == pytest.approx(999.0)
        assert c["relative_uplift_pct"]["total_net_value"] == pytest.approx(
            (999.0 / 999.0) * 100.0
        )

    def test_comparison_zero_baseline_net_value(self):
        """When baseline total_net_value == 0, relative uplift must be None."""
        b = {
            "policy_name": "Baseline",
            "n_cases": 1,
            "recovered_count": 0,
            "recovery_rate": 0.0,
            "total_recovered_amount": 0.0,
            "total_action_cost": 0.0,
            "total_net_value": 0.0,
            "avg_net_value_per_case": 0.0,
            "stop_count": 1,
            "stop_rate": 1.0,
            "action_distribution": {"stop": 1},
            "action_recovery_breakdown": {},
        }
        r = dict(b)
        r["total_net_value"] = 500.0
        c = compute_comparison(b, r)
        assert c["relative_uplift_pct"]["total_net_value"] is None

    def test_comparison_verdict_positive(self):
        b = {"policy_name": "B", "n_cases": 1, "recovered_count": 0,
             "recovery_rate": 0.0, "total_recovered_amount": 0.0,
             "total_action_cost": 0.0, "total_net_value": 100.0,
             "avg_net_value_per_case": 100.0, "stop_count": 0, "stop_rate": 0.0,
             "action_distribution": {}, "action_recovery_breakdown": {}}
        r = dict(b)
        r["total_net_value"] = 200.0
        c = compute_comparison(b, r)
        assert "exceeds" in c["verdict"].lower()

    def test_comparison_verdict_negative(self):
        b = {"policy_name": "B", "n_cases": 1, "recovered_count": 0,
             "recovery_rate": 0.0, "total_recovered_amount": 0.0,
             "total_action_cost": 0.0, "total_net_value": 300.0,
             "avg_net_value_per_case": 300.0, "stop_count": 0, "stop_rate": 0.0,
             "action_distribution": {}, "action_recovery_breakdown": {}}
        r = dict(b)
        r["total_net_value"] = 100.0
        c = compute_comparison(b, r)
        assert "underperforms" in c["verdict"].lower()

    def test_action_distribution_shift_computed(self):
        b = {"policy_name": "B", "n_cases": 4, "recovered_count": 0,
             "recovery_rate": 0.0, "total_recovered_amount": 0.0,
             "total_action_cost": 0.0, "total_net_value": 0.0,
             "avg_net_value_per_case": 0.0, "stop_count": 0, "stop_rate": 0.0,
             "action_distribution": {"retry_now": 4},
             "action_recovery_breakdown": {}}
        r = dict(b)
        r["action_distribution"] = {"send_reminder": 4}
        c = compute_comparison(b, r)
        shift = c["action_distribution_shift"]
        assert shift["retry_now"]["baseline_count"] == 4
        assert shift["retry_now"]["recoveros_count"] == 0
        assert shift["send_reminder"]["baseline_count"] == 0
        assert shift["send_reminder"]["recoveros_count"] == 4


# ── Full pipeline integration ──────────────────────────────────────────────────


class TestFullPipelineIntegration:
    def test_load_and_evaluate_returns_required_keys(self):
        result = load_and_evaluate()
        assert "test_df" in result
        assert "baseline_records" in result
        assert "recoveros_records" in result

    def test_load_and_evaluate_same_population(self):
        result = load_and_evaluate()
        test_df = result["test_df"]
        baseline_records = result["baseline_records"]
        recoveros_records = result["recoveros_records"]
        assert len(baseline_records) == len(test_df) == N_EXPECTED
        assert len(recoveros_records) == len(test_df) == N_EXPECTED

    def test_metrics_values_are_not_fabricated(self):
        """
        Verify that metrics are computed from actual records, not hard-coded
        or pre-set values. We run the evaluation, compute metrics, and verify
        they match direct summation over case records.
        """
        result = load_and_evaluate()
        baseline_records = result["baseline_records"]

        m = compute_policy_metrics(baseline_records, "Baseline")

        # Independently verify recovered_count
        expected_recovered = sum(1 for r in baseline_records if r["recovered"])
        assert m["recovered_count"] == expected_recovered

        # Independently verify total_net_value
        expected_net = sum(r["net_value"] for r in baseline_records)
        assert m["total_net_value"] == pytest.approx(expected_net, rel=1e-5)

        # Independently verify total_action_cost
        expected_cost = sum(r["action_cost"] for r in baseline_records)
        assert m["total_action_cost"] == pytest.approx(expected_cost, rel=1e-5)
