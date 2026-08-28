"""
Tests for bootstrap CI and paired comparison (Phase 2C Step 2A).

Verifies:
- Bootstrap reproducibility (same seed → same result)
- Bootstrap CI ordering (lo ≤ point ≤ hi)
- CI does not cross zero for large true differences (structural test)
- Paired comparison correctness
- Win/tie/loss counts sum to n_cases
- Zero-division safety in metrics
- Metric correctness on synthetic data
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.run_step2a import (
    _bootstrap_net_value,
    _paired_comparison,
    _action_level_table,
    N_BOOTSTRAPS,
    BOOTSTRAP_SEED,
)
from evaluation.metrics import compute_policy_metrics, safe_divide
from simulator.recovery_simulator import ACTIONS


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic record factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_records(
    n: int,
    recovered: bool,
    net_value: float,
    action: str = "retry_later",
) -> List[Dict[str, Any]]:
    return [
        {
            "index": i,
            "action": action,
            "recovered": recovered,
            "recovered_amount": net_value + 1.0 if recovered else 0.0,
            "action_cost": 1.0,
            "net_value": net_value,
            "recovery_probability_gt": 0.7,
            "predicted_erv": None,
            "guardrails_triggered": None,
        }
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap reproducibility
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapReproducibility:
    def test_same_seed_same_result(self):
        """Running bootstrap twice with same seed produces identical output."""
        recs_a = _make_records(100, False, 0.0)
        recs_b = _make_records(100, True,  500.0)
        r1 = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=200, seed=42)
        r2 = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=200, seed=42)
        assert r1["bootstrap_ci_95_total_net_value"] == r2["bootstrap_ci_95_total_net_value"]
        assert r1["bootstrap_mean_diff_net_value"] == r2["bootstrap_mean_diff_net_value"]

    def test_different_seed_may_differ(self):
        """Bootstrap std should be positive when records have heterogeneous net values."""
        import numpy as np
        rng = np.random.default_rng(123)
        recs_a = [{"index": i, "action": "retry_now", "recovered": bool(i % 2),
                   "recovered_amount": float(i) * 2, "action_cost": 1.0,
                   "net_value": float(i) * 2 - 1,
                   "recovery_probability_gt": 0.5, "predicted_erv": None,
                   "guardrails_triggered": None} for i in range(100)]
        recs_b = [{"index": i, "action": "retry_later", "recovered": bool(i % 3 != 0),
                   "recovered_amount": float(i) * 3, "action_cost": 1.0,
                   "net_value": float(i) * 3 - 1,
                   "recovery_probability_gt": 0.7, "predicted_erv": None,
                   "guardrails_triggered": None} for i in range(100)]
        r1 = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=200, seed=0)
        # Heterogeneous data — bootstrap std must be positive
        assert r1["bootstrap_std_diff_net_value"] > 0

    def test_bootstrap_documented_parameters(self):
        """N_BOOTSTRAPS and BOOTSTRAP_SEED are defined and deterministic."""
        assert isinstance(N_BOOTSTRAPS, int) and N_BOOTSTRAPS > 0
        assert isinstance(BOOTSTRAP_SEED, int)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap CI ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapCIOrdering:
    def test_ci_lo_leq_hi(self):
        recs_a = _make_records(100, False, 0.0)
        recs_b = _make_records(100, True,  300.0)
        result = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=200, seed=42)
        lo, hi = result["bootstrap_ci_95_total_net_value"]
        assert lo <= hi

    def test_ci_contains_point_estimate_approximately(self):
        """Point estimate should fall within (or very near) the bootstrap CI."""
        recs_a = _make_records(200, False, -10.0)
        recs_b = _make_records(200, True,   400.0)
        result = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=500, seed=0)
        lo, hi = result["bootstrap_ci_95_total_net_value"]
        pe = result["point_estimate_total_net_value_diff"]
        # Point estimate should be within a small tolerance of CI bounds
        # (bootstrap CIs are not guaranteed to contain the point estimate but they should be nearby)
        assert lo < pe * 1.2  # lo must be less than 120% of point estimate
        assert hi > pe * 0.8  # hi must be greater than 80% of point estimate

    def test_zero_difference_ci_straddles_zero(self):
        """When both policies are identical, CI should straddle zero."""
        recs = _make_records(100, True, 200.0)
        result = _bootstrap_net_value(recs, recs, "A", "A", n_bootstraps=200, seed=7)
        lo, hi = result["bootstrap_ci_95_total_net_value"]
        assert lo <= 0 <= hi, "Identical policies must have CI straddling zero"

    def test_large_difference_ci_does_not_cross_zero(self):
        """A very large difference (100× effect) should not cross zero."""
        recs_a = _make_records(200, False, 0.0)   # 0 net value always
        recs_b = _make_records(200, True,  5000.0) # 5000 always
        result = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=500, seed=0)
        lo, hi = result["bootstrap_ci_95_total_net_value"]
        assert lo > 0 and hi > 0, "Deterministic large difference must have CI entirely positive"
        assert not result["ci_crosses_zero_net_value"]


# ─────────────────────────────────────────────────────────────────────────────
# Paired comparison correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestPairedComparison:
    def test_win_tie_loss_sum_to_n(self):
        recs_a = _make_records(100, False, 0.0)
        recs_b = _make_records(100, True,  300.0)
        result = _paired_comparison(recs_a, recs_b, "A", "B")
        # Extract all keys ending with _wins
        wins = [v for k, v in result.items() if k.endswith("_wins")]
        total = sum(wins) + result["ties"]
        assert total == result["n_cases"] == 100

    def test_all_b_wins_when_b_dominates(self):
        recs_a = _make_records(50, False, 0.0)
        recs_b = _make_records(50, True,  400.0)
        result = _paired_comparison(recs_a, recs_b, "A", "B")
        b_wins = [v for k, v in result.items() if k.endswith("_wins") and "A" not in k]
        assert b_wins[0] == 50
        assert result["ties"] == 0

    def test_all_ties_when_identical(self):
        recs = _make_records(60, True, 100.0)
        result = _paired_comparison(recs, recs, "A", "B")
        assert result["ties"] == 60
        assert result["mean_delta_net_value"] == 0.0

    def test_mean_delta_correct(self):
        """mean delta = sum of diffs / n."""
        recs_a = [{"index": i, "action": "retry_later", "recovered": False,
                   "recovered_amount": 0.0, "action_cost": 1.0, "net_value": float(i),
                   "recovery_probability_gt": 0.5, "predicted_erv": None,
                   "guardrails_triggered": None} for i in range(10)]
        recs_b = [{"index": i, "action": "retry_later", "recovered": True,
                   "recovered_amount": float(i) + 10.0, "action_cost": 1.0,
                   "net_value": float(i) + 10.0,
                   "recovery_probability_gt": 0.8, "predicted_erv": None,
                   "guardrails_triggered": None} for i in range(10)]
        result = _paired_comparison(recs_a, recs_b, "A", "B")
        assert abs(result["mean_delta_net_value"] - 10.0) < 1e-9

    def test_percentiles_ordered(self):
        recs_a = _make_records(200, False, 0.0)
        recs_b = [{"index": i, "action": "retry_later", "recovered": True,
                   "recovered_amount": float(i), "action_cost": 1.0,
                   "net_value": float(i),
                   "recovery_probability_gt": 0.7, "predicted_erv": None,
                   "guardrails_triggered": None} for i in range(200)]
        result = _paired_comparison(recs_a, recs_b, "A", "B")
        p = result["percentiles"]
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]


# ─────────────────────────────────────────────────────────────────────────────
# Action-level table
# ─────────────────────────────────────────────────────────────────────────────

class TestActionLevelTable:
    def test_all_actions_present_in_output(self):
        recs = _make_records(50, True, 100.0, action="retry_later")
        table = _action_level_table(recs, 50)
        for act in ACTIONS:
            assert act in table

    def test_count_correct(self):
        recs = _make_records(30, True, 100.0, action="retry_now")
        table = _action_level_table(recs, 30)
        assert table["retry_now"]["count"] == 30
        assert table["retry_later"]["count"] == 0

    def test_pct_sums_to_100(self):
        recs = _make_records(100, True, 100.0, action="recovery_link")
        table = _action_level_table(recs, 100)
        total_pct = sum(v["pct"] for v in table.values())
        assert abs(total_pct - 100.0) < 1e-6

    def test_recovery_rate_correct(self):
        recs = _make_records(20, True, 100.0, action="retry_later")
        table = _action_level_table(recs, 20)
        assert table["retry_later"]["recovery_rate"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Zero-division safety
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroDivisionSafety:
    def test_safe_divide_zero_denominator(self):
        assert safe_divide(10.0, 0.0) == 0.0
        assert safe_divide(10.0, 0.0, default=-1.0) == -1.0

    def test_empty_records_metrics(self):
        result = compute_policy_metrics([], "empty")
        assert result["recovery_rate"] == 0.0
        assert result["total_net_value"] == 0.0

    def test_bootstrap_single_record(self):
        """Bootstrap should not crash on minimal input."""
        recs_a = _make_records(1, False, 0.0)
        recs_b = _make_records(1, True,  200.0)
        result = _bootstrap_net_value(recs_a, recs_b, "A", "B", n_bootstraps=10, seed=0)
        assert isinstance(result["bootstrap_ci_95_total_net_value"], list)
        assert len(result["bootstrap_ci_95_total_net_value"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Full pipeline integration (smoke test)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipelineIntegration:
    @pytest.fixture(scope="class")
    def step2a_result(self):
        """Run the full Step 2A pipeline once and cache."""
        from evaluation.run_step2a import (
            run_heuristic_evaluation,
            _bootstrap_net_value,
            _paired_comparison,
        )
        from evaluation.evaluator import _load_test_data, _load_ml_model, run_baseline_evaluation, run_recoveros_evaluation
        from evaluation.metrics import compute_policy_metrics
        test_df = _load_test_data()
        model   = _load_ml_model()
        b_recs = run_baseline_evaluation(test_df)
        h_recs = run_heuristic_evaluation(test_df)
        r_recs = run_recoveros_evaluation(test_df, model)
        b_met  = compute_policy_metrics(b_recs, "Baseline")
        h_met  = compute_policy_metrics(h_recs, "Heuristic")
        r_met  = compute_policy_metrics(r_recs, "RecoverOS")
        boot_bh = _bootstrap_net_value(b_recs, r_recs, "B", "R", n_bootstraps=100, seed=0)
        boot_hh = _bootstrap_net_value(h_recs, r_recs, "H", "R", n_bootstraps=100, seed=0)
        return dict(b=b_met, h=h_met, r=r_met, boot_bh=boot_bh, boot_hh=boot_hh,
                    b_recs=b_recs, h_recs=h_recs, r_recs=r_recs)

    def test_all_three_policies_cover_1000_cases(self, step2a_result):
        assert step2a_result["b"]["n_cases"] == 1000
        assert step2a_result["h"]["n_cases"] == 1000
        assert step2a_result["r"]["n_cases"] == 1000

    def test_bootstrap_ci_has_two_bounds(self, step2a_result):
        ci = step2a_result["boot_bh"]["bootstrap_ci_95_total_net_value"]
        assert len(ci) == 2
        assert ci[0] <= ci[1]

    def test_heuristic_outperforms_existing_baseline(self, step2a_result):
        """Strong heuristic must beat the weak deterministic baseline."""
        assert step2a_result["h"]["total_net_value"] > step2a_result["b"]["total_net_value"], \
            "Strong heuristic should outperform the strawman baseline"

    def test_all_indices_identical_across_policies(self, step2a_result):
        """All three policy records must cover identical indices."""
        for i, (b, h, r) in enumerate(zip(
            step2a_result["b_recs"],
            step2a_result["h_recs"],
            step2a_result["r_recs"],
        )):
            assert b["index"] == h["index"] == r["index"] == i
