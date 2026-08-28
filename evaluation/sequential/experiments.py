"""
RecoverOS Phase 2C Step 4 — Sequential Experiments & Comparative Analysis Engine.

Runs:
1. Full 3-round sequential evaluation for Baseline, Heuristic, and RecoverOS.
2. Bootstrap 95% CIs and paired comparisons across full journeys and per-round.
3. Action transition pathway analysis.
4. Segmented cohort evaluations (insufficient funds, hard failures, high attempts, fatigue, high value, high overdue).
5. Sequential distribution shift / out-of-distribution (OOD) analysis against training data.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure UTF-8 output encoding on Windows consoles
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from evaluation.evaluator import _load_test_data, _load_ml_model
from evaluation.sequential.evaluator import SequentialPolicyEvaluator
from evaluation.sequential.metrics import compute_sequential_metrics, safe_divide
from evaluation.sequential.state import SequentialCaseState


def _bootstrap_paired_sequential(
    states_a: List[SequentialCaseState],
    states_b: List[SequentialCaseState],
    label_a: str,
    label_b: str,
    n_bootstraps: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Computes deterministic bootstrap 95% CI on final net value & recovery rate differences.
    """
    n = len(states_a)
    assert len(states_b) == n

    nv_a = np.array([s.cumulative_net_value for s in states_a], dtype=float)
    nv_b = np.array([s.cumulative_net_value for s in states_b], dtype=float)

    rec_a = np.array([float(s.is_recovered) for s in states_a], dtype=float)
    rec_b = np.array([float(s.is_recovered) for s in states_b], dtype=float)

    rng = np.random.default_rng(seed)
    diffs_nv: List[float] = []
    diffs_rr: List[float] = []

    for _ in range(n_bootstraps):
        idx = rng.integers(0, n, size=n)
        diffs_nv.append(float(nv_b[idx].sum() - nv_a[idx].sum()))
        diffs_rr.append(float(rec_b[idx].mean() - rec_a[idx].mean()))

    arr_nv = np.array(diffs_nv)
    arr_rr = np.array(diffs_rr)

    lo_nv, hi_nv = float(np.percentile(arr_nv, 2.5)), float(np.percentile(arr_nv, 97.5))
    lo_rr, hi_rr = float(np.percentile(arr_rr, 2.5)), float(np.percentile(arr_rr, 97.5))

    pt_nv = float(nv_b.sum() - nv_a.sum())
    pt_rr = float(rec_b.mean() - rec_a.mean())

    # Paired win/tie/loss counts
    diff_case = nv_b - nv_a
    b_wins = int(sum(1 for d in diff_case if d > 0))
    a_wins = int(sum(1 for d in diff_case if d < 0))
    ties = int(sum(1 for d in diff_case if d == 0))

    return {
        "comparator": label_a,
        "recoveros": label_b,
        "point_estimate_net_value_diff": round(pt_nv, 4),
        "point_estimate_recovery_rate_diff": round(pt_rr, 6),
        "bootstrap_ci_95_net_value": [round(lo_nv, 4), round(hi_nv, 4)],
        "bootstrap_ci_95_recovery_rate": [round(lo_rr, 6), round(hi_rr, 6)],
        "ci_crosses_zero_net_value": bool(lo_nv < 0 < hi_nv or hi_nv < 0),
        "ci_crosses_zero_recovery_rate": bool(lo_rr < 0 < hi_rr or hi_rr < 0),
        "paired_wins": {
            f"{label_b}_wins": b_wins,
            f"{label_a}_wins": a_wins,
            "ties": ties,
        },
    }


def _analyze_distribution_shift(
    test_df: pd.DataFrame,
    states_ml: List[SequentialCaseState],
) -> Dict[str, Any]:
    """
    Measures feature distribution shift across sequential rounds relative to train.csv.
    """
    train_path = PROJECT_ROOT / "data" / "processed" / "train.csv"
    train_df = pd.read_csv(train_path)

    features = ["attempt_number", "contact_count", "days_overdue", "previous_failure_count"]
    stats: Dict[str, Any] = {}

    # Train baselines
    for feat in features:
        train_vals = train_df[feat].dropna().values
        stats[feat] = {
            "train_mean": round(float(np.mean(train_vals)), 2),
            "train_std": round(float(np.std(train_vals)), 2),
            "train_p90": round(float(np.percentile(train_vals, 90)), 2),
            "train_max": round(float(np.max(train_vals)), 2),
            "by_round": {},
        }

    # Extract state per round for cases that entered each round
    for r in [1, 2, 3]:
        r_records = []
        for s in states_ml:
            if len(s.round_outcomes) >= r:
                # Reconstruct state at beginning of round r
                if r == 1:
                    r_records.append({
                        "attempt_number": s.round_outcomes[0].get("attempt_number", s.attempt_number - (len(s.round_outcomes)-1)),
                        "contact_count": s.contact_count,
                        "days_overdue": s.days_overdue,
                        "previous_failure_count": s.previous_failure_count,
                    })
                else:
                    # Current state of cases active at round r
                    r_records.append(s.to_record_dict())

        r_df = pd.DataFrame(r_records)
        if len(r_df) > 0:
            for feat in features:
                vals = r_df[feat].dropna().values
                stats[feat]["by_round"][f"round_{r}"] = {
                    "active_cases": len(r_df),
                    "mean": round(float(np.mean(vals)), 2),
                    "p90": round(float(np.percentile(vals, 90)), 2),
                    "max": round(float(np.max(vals)), 2),
                    "pct_exceeding_train_p90": round(float(np.mean(vals > stats[feat]["train_p90"]) * 100), 2),
                }

    return stats


def run_sequential_experiments() -> Dict[str, Any]:
    test_df = _load_test_data()
    model = _load_ml_model()
    evaluator = SequentialPolicyEvaluator()

    # 1. Execute full sequential evaluations
    baseline_states = evaluator.evaluate_baseline(test_df)
    heuristic_states = evaluator.evaluate_heuristic(test_df)
    recoveros_states = evaluator.evaluate_recoveros(test_df, model)

    # 2. Compute metrics
    b_metrics = compute_sequential_metrics(baseline_states, "Deterministic Baseline")
    h_metrics = compute_sequential_metrics(heuristic_states, "Strong Feature-Aware Heuristic")
    r_metrics = compute_sequential_metrics(recoveros_states, "RecoverOS ML Policy")

    # 3. Overall Comparisons & Bootstrap CIs
    comp_vs_base = _bootstrap_paired_sequential(baseline_states, recoveros_states, "Baseline", "RecoverOS")
    comp_vs_heur = _bootstrap_paired_sequential(heuristic_states, recoveros_states, "Heuristic", "RecoverOS")
    comp_heur_vs_base = _bootstrap_paired_sequential(baseline_states, heuristic_states, "Baseline", "Heuristic")

    # 4. Round-by-Round Cumulative Progression Comparison
    round_progression_comparison = {}
    for r in [1, 2, 3]:
        r_key = f"round_{r}"
        b_r = b_metrics["round_progression"][r_key]
        h_r = h_metrics["round_progression"][r_key]
        r_r = r_metrics["round_progression"][r_key]
        round_progression_comparison[r_key] = {
            "cumulative_recovery_rate": {
                "baseline": b_r["cumulative_recovery_rate"],
                "heuristic": h_r["cumulative_recovery_rate"],
                "recoveros": r_r["cumulative_recovery_rate"],
            },
            "cumulative_net_value": {
                "baseline": b_r["cumulative_net_value"],
                "heuristic": h_r["cumulative_net_value"],
                "recoveros": r_r["cumulative_net_value"],
            },
            "delta_recoveros_minus_heuristic": round(r_r["cumulative_net_value"] - h_r["cumulative_net_value"], 4),
            "delta_recoveros_minus_baseline": round(r_r["cumulative_net_value"] - b_r["cumulative_net_value"], 4),
        }

    # 5. Cohort / Segment Subgroup Evaluations
    cohort_specs = {
        "insufficient_funds": test_df["failure_type"] == "insufficient_funds",
        "hard_failures": test_df["failure_type"].isin(["expired_card", "hard_decline", "invalid_payment_method"]),
        "bank_timeout": test_df["failure_type"] == "bank_timeout",
        "customer_abandoned": test_df["failure_type"] == "customer_abandoned",
        "attempt_gte_3": test_df["attempt_number"] >= 3,
        "contact_fatigued_gte_4": test_df["contact_count"] >= 4,
        "high_amount_gte_1500": test_df["amount"] >= 1500.528,
        "days_overdue_gte_10": test_df["days_overdue"] >= 10,
    }

    cohort_results: Dict[str, Any] = {}
    for c_name, mask in cohort_specs.items():
        sub_indices = np.where(mask)[0]
        sub_b = [baseline_states[i] for i in sub_indices]
        sub_h = [heuristic_states[i] for i in sub_indices]
        sub_r = [recoveros_states[i] for i in sub_indices]

        sub_bm = compute_sequential_metrics(sub_b, f"Baseline ({c_name})")
        sub_hm = compute_sequential_metrics(sub_h, f"Heuristic ({c_name})")
        sub_rm = compute_sequential_metrics(sub_r, f"RecoverOS ({c_name})")

        sub_comp = _bootstrap_paired_sequential(sub_h, sub_r, "Heuristic", "RecoverOS", n_bootstraps=500)

        cohort_results[c_name] = {
            "n_cases": len(sub_indices),
            "baseline_net_value": sub_bm["total_net_value"],
            "baseline_recovery_rate": sub_bm["recovery_rate"],
            "heuristic_net_value": sub_hm["total_net_value"],
            "heuristic_recovery_rate": sub_hm["recovery_rate"],
            "recoveros_net_value": sub_rm["total_net_value"],
            "recoveros_recovery_rate": sub_rm["recovery_rate"],
            "diff_recoveros_minus_heuristic": round(sub_rm["total_net_value"] - sub_hm["total_net_value"], 4),
            "bootstrap_ci_95": sub_comp["bootstrap_ci_95_net_value"],
            "ci_crosses_zero": sub_comp["ci_crosses_zero_net_value"],
            "paired_wins": sub_comp["paired_wins"],
        }

    # 6. Distribution Shift Analysis against train.csv
    distribution_shift = _analyze_distribution_shift(test_df, recoveros_states)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_horizon_rounds": 3,
        "n_cases": len(test_df),
        "baseline_metrics": b_metrics,
        "heuristic_metrics": h_metrics,
        "recoveros_metrics": r_metrics,
        "comparisons": {
            "recoveros_vs_baseline": comp_vs_base,
            "recoveros_vs_heuristic": comp_vs_heur,
            "heuristic_vs_baseline": comp_heur_vs_base,
        },
        "round_progression_comparison": round_progression_comparison,
        "cohort_results": cohort_results,
        "distribution_shift_analysis": distribution_shift,
    }


if __name__ == "__main__":
    res = run_sequential_experiments()
    print("Sequential evaluation complete!")
    print(f"Baseline Net Value : ₹{res['baseline_metrics']['total_net_value']:,.2f} (RR: {res['baseline_metrics']['recovery_rate']*100:.2f}%)")
    print(f"Heuristic Net Value: ₹{res['heuristic_metrics']['total_net_value']:,.2f} (RR: {res['heuristic_metrics']['recovery_rate']*100:.2f}%)")
    print(f"RecoverOS Net Value: ₹{res['recoveros_metrics']['total_net_value']:,.2f} (RR: {res['recoveros_metrics']['recovery_rate']*100:.2f}%)")
    diff = res["comparisons"]["recoveros_vs_heuristic"]["point_estimate_net_value_diff"]
    ci = res["comparisons"]["recoveros_vs_heuristic"]["bootstrap_ci_95_net_value"]
    print(f"RecoverOS vs Heuristic Diff: {diff:+,.2f}  95% CI: [{ci[0]:+,.2f}, {ci[1]:+,.2f}]")
