"""
RecoverOS Phase 2C Step 3 — Robustness Experiments Runner.

Implements:
1. Multi-Seed Simulation Variance (5 independent seed streams)
2. Distribution-Shift & Slice Evaluation (15+ principled schema-derived slices)
3. Feature Perturbation Tests (Categorical missingness & Numerical noise)
4. Rare-Combination Stress Testing
5. Paired differences & Bootstrap CIs per experiment
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.recovery_simulator import ACTIONS, simulate_action
from evaluation.evaluator import SEED_BASE, _load_test_data, _load_ml_model
from simulator.policies import DeterministicBaselinePolicy, MLExpectedValuePolicy
from evaluation.policies.feature_aware_heuristic import StrongFeatureAwareHeuristic
from evaluation.robustness.engine import (
    DISTRIBUTION_SLICES,
    RARE_COMBOS,
    apply_slice,
    perturb_categorical_missingness,
    perturb_numeric_noise,
    _bootstrap_slice,
    _paired_wins,
)


def evaluate_policies_on_slice(
    true_df: pd.DataFrame,
    input_df: pd.DataFrame,
    model: Any,
    seed_offset: int = 0,
    n_bootstraps: int = 500,
    bootstrap_seed: int = 42,
) -> Dict[str, Any]:
    """
    Evaluates Baseline, Heuristic, and RecoverOS.
    Policies select actions based on input_df (which may be perturbed).
    Outcomes are simulated on true_df with seed = SEED_BASE + index + seed_offset.
    """
    n = len(true_df)
    if n == 0:
        return {"n": 0, "error": "empty slice"}

    base_policy = DeterministicBaselinePolicy()
    heur_policy = StrongFeatureAwareHeuristic()
    ml_policy = MLExpectedValuePolicy(model, guardrails_enabled=True)

    base_actions = base_policy.select_actions_batch(input_df)
    heur_actions = heur_policy.select_actions_batch(input_df)
    ml_actions = ml_policy.select_actions_batch(input_df)

    def run_sim(actions: List[str], policy_name: str) -> Tuple[List[Dict], Dict[str, Any]]:
        recs = []
        for i in range(n):
            true_row = true_df.iloc[i]
            action = actions[i]
            # Deterministic per-record seed
            # Use original true_row index if present, else position
            row_idx = true_row.name if isinstance(true_row.name, int) else i
            seed = SEED_BASE + row_idx + seed_offset
            sim = simulate_action(true_row, action, seed=seed)
            recs.append({
                "index": i,
                "action": action,
                "recovered": sim["recovered"],
                "recovered_amount": sim["recovered_amount"],
                "action_cost": sim["action_cost"],
                "net_value": sim["net_value"],
                "gt_prob": sim["recovery_probability"],
            })
        
        rec_cnt = sum(1 for r in recs if r["recovered"])
        tot_nv = sum(r["net_value"] for r in recs)
        tot_cost = sum(r["action_cost"] for r in recs)
        tot_amt = sum(r["recovered_amount"] for r in recs)
        act_dist = {}
        for r in recs:
            act_dist[r["action"]] = act_dist.get(r["action"], 0) + 1

        summary = {
            "policy": policy_name,
            "n": n,
            "recovery_rate": round(rec_cnt / n, 6),
            "recovered_count": rec_cnt,
            "total_net_value": round(tot_nv, 4),
            "avg_net_value": round(tot_nv / n, 4),
            "total_action_cost": round(tot_cost, 4),
            "total_recovered_amount": round(tot_amt, 4),
            "action_distribution": act_dist,
        }
        return recs, summary

    base_recs, base_summary = run_sim(base_actions, "Deterministic Baseline")
    heur_recs, heur_summary = run_sim(heur_actions, "Strong Feature-Aware Heuristic")
    ml_recs, ml_summary = run_sim(ml_actions, "RecoverOS ML Policy")

    # Comparisons
    # RecoverOS vs Baseline
    diff_nv_base = ml_summary["total_net_value"] - base_summary["total_net_value"]
    uplift_base = (diff_nv_base / base_summary["total_net_value"] * 100) if base_summary["total_net_value"] != 0 else None
    boot_base = _bootstrap_slice(base_recs, ml_recs, n_bootstraps=n_bootstraps, seed=bootstrap_seed)
    paired_base = _paired_wins(base_recs, ml_recs, "Baseline", "RecoverOS")

    # RecoverOS vs Heuristic
    diff_nv_heur = ml_summary["total_net_value"] - heur_summary["total_net_value"]
    uplift_heur = (diff_nv_heur / heur_summary["total_net_value"] * 100) if heur_summary["total_net_value"] != 0 else None
    boot_heur = _bootstrap_slice(heur_recs, ml_recs, n_bootstraps=n_bootstraps, seed=bootstrap_seed)
    paired_heur = _paired_wins(heur_recs, ml_recs, "Heuristic", "RecoverOS")

    # Slice verdict vs heuristic
    if boot_heur["ci_95"] is not None:
        lo, hi = boot_heur["ci_95"]
        if lo > 0:
            verdict = "RecoverOS Wins (statistically significant)"
        elif hi < 0:
            verdict = "Heuristic Wins (statistically significant)"
        else:
            verdict = "Inconclusive (CI crosses zero)"
    else:
        if diff_nv_heur > 0:
            verdict = "RecoverOS Ahead (small sample)"
        elif diff_nv_heur < 0:
            verdict = "Heuristic Ahead (small sample)"
        else:
            verdict = "Tied"

    return {
        "n": n,
        "baseline": base_summary,
        "heuristic": heur_summary,
        "recoveros": ml_summary,
        "recoveros_vs_baseline": {
            "abs_net_value_diff": round(diff_nv_base, 4),
            "relative_uplift_pct": round(uplift_base, 4) if uplift_base is not None else None,
            "bootstrap_ci_95": boot_base["ci_95"],
            "ci_crosses_zero": boot_base["crosses_zero"],
            "paired": paired_base,
        },
        "recoveros_vs_heuristic": {
            "abs_net_value_diff": round(diff_nv_heur, 4),
            "relative_uplift_pct": round(uplift_heur, 4) if uplift_heur is not None else None,
            "bootstrap_ci_95": boot_heur["ci_95"],
            "ci_crosses_zero": boot_heur["crosses_zero"],
            "paired": paired_heur,
        },
        "verdict_vs_heuristic": verdict,
    }


def run_all_robustness_experiments() -> Dict[str, Any]:
    test_df = _load_test_data()
    model = _load_ml_model()

    results: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "multi_seed_experiments": {},
        "distribution_slice_experiments": {},
        "feature_perturbation_experiments": {},
        "stress_test_experiments": {},
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Multi-Seed Experiments (5 independent seed streams)
    # ─────────────────────────────────────────────────────────────────────────
    seed_offsets = [0, 1000, 2000, 3000, 4000]
    multi_seed_runs = []
    for offset in seed_offsets:
        res = evaluate_policies_on_slice(test_df, test_df, model, seed_offset=offset, n_bootstraps=200)
        multi_seed_runs.append({
            "seed_offset": offset,
            "baseline_recovery_rate": res["baseline"]["recovery_rate"],
            "baseline_net_value": res["baseline"]["total_net_value"],
            "heuristic_recovery_rate": res["heuristic"]["recovery_rate"],
            "heuristic_net_value": res["heuristic"]["total_net_value"],
            "recoveros_recovery_rate": res["recoveros"]["recovery_rate"],
            "recoveros_net_value": res["recoveros"]["total_net_value"],
            "diff_net_value_vs_heuristic": res["recoveros_vs_heuristic"]["abs_net_value_diff"],
            "uplift_pct_vs_heuristic": res["recoveros_vs_heuristic"]["relative_uplift_pct"],
        })

    # Summary statistics across seeds
    b_nvs = [r["baseline_net_value"] for r in multi_seed_runs]
    h_nvs = [r["heuristic_net_value"] for r in multi_seed_runs]
    r_nvs = [r["recoveros_net_value"] for r in multi_seed_runs]
    diff_nvs = [r["diff_net_value_vs_heuristic"] for r in multi_seed_runs]

    results["multi_seed_experiments"] = {
        "seed_offsets": seed_offsets,
        "runs": multi_seed_runs,
        "summary": {
            "baseline_net_value_mean": round(float(np.mean(b_nvs)), 2),
            "baseline_net_value_std": round(float(np.std(b_nvs)), 2),
            "heuristic_net_value_mean": round(float(np.mean(h_nvs)), 2),
            "heuristic_net_value_std": round(float(np.std(h_nvs)), 2),
            "recoveros_net_value_mean": round(float(np.mean(r_nvs)), 2),
            "recoveros_net_value_std": round(float(np.std(r_nvs)), 2),
            "mean_net_value_diff_recoveros_minus_heuristic": round(float(np.mean(diff_nvs)), 2),
            "std_net_value_diff": round(float(np.std(diff_nvs)), 2),
            "min_diff": round(float(np.min(diff_nvs)), 2),
            "max_diff": round(float(np.max(diff_nvs)), 2),
        }
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Distribution-Shift & Slice Evaluation
    # ─────────────────────────────────────────────────────────────────────────
    slice_results = {}
    for slice_name, spec in DISTRIBUTION_SLICES.items():
        mask = apply_slice(test_df, spec)
        sub_df = test_df[mask].copy()
        slice_res = evaluate_policies_on_slice(sub_df, sub_df, model, seed_offset=0, n_bootstraps=500)
        slice_results[slice_name] = {
            "slice_spec": spec,
            "results": slice_res,
        }

    # Extended structured slices
    # 2b. Combined Amount & Attempt Slice: High Amount (>= Q75) & Late Attempt (>= 2)
    mask_high_late = (test_df["amount"] >= 1500.528) & (test_df["attempt_number"] >= 2)
    sub_hl = test_df[mask_high_late].copy()
    slice_results["high_amount_late_attempt"] = {
        "slice_spec": {"description": "amount >= ₹1500.53 AND attempt_number >= 2"},
        "results": evaluate_policies_on_slice(sub_hl, sub_hl, model, seed_offset=0),
    }

    # 2c. Combined Fresh & High CLV: contact <= 1 AND CLV >= 10000
    mask_fresh_clv = (test_df["contact_count"] <= 1) & (test_df["customer_lifetime_value"] >= 10000)
    sub_fclv = test_df[mask_fresh_clv].copy()
    slice_results["fresh_contact_high_clv"] = {
        "slice_spec": {"description": "contact_count <= 1 AND CLV >= ₹10,000"},
        "results": evaluate_policies_on_slice(sub_fclv, sub_fclv, model, seed_offset=0),
    }

    results["distribution_slice_experiments"] = slice_results

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Feature Perturbation Tests
    # ─────────────────────────────────────────────────────────────────────────
    perturbation_results = {}

    # 3a. Missingness in failure_type (10%, 25%, 50%)
    for frac in [0.10, 0.25, 0.50]:
        name = f"missing_failure_type_{int(frac*100)}pct"
        p_df = perturb_categorical_missingness(test_df, "failure_type", frac, rng_seed=42)
        perturbation_results[name] = {
            "type": "categorical_missingness",
            "column": "failure_type",
            "fraction": frac,
            "results": evaluate_policies_on_slice(test_df, p_df, model),
        }

    # 3b. Missingness in payment_method (10%, 25%, 50%)
    for frac in [0.10, 0.25, 0.50]:
        name = f"missing_payment_method_{int(frac*100)}pct"
        p_df = perturb_categorical_missingness(test_df, "payment_method", frac, rng_seed=42)
        perturbation_results[name] = {
            "type": "categorical_missingness",
            "column": "payment_method",
            "fraction": frac,
            "results": evaluate_policies_on_slice(test_df, p_df, model),
        }

    # 3c. Numeric noise in amount (10%, 25%, 50% std noise)
    for frac in [0.10, 0.25, 0.50]:
        name = f"noisy_amount_{int(frac*100)}pct_std"
        p_df = perturb_numeric_noise(test_df, "amount", frac, rng_seed=42, clip_min=10.0)
        perturbation_results[name] = {
            "type": "numeric_noise",
            "column": "amount",
            "noise_std_fraction": frac,
            "results": evaluate_policies_on_slice(test_df, p_df, model),
        }

    # 3d. Numeric noise in days_overdue (25%, 50% std noise)
    for frac in [0.25, 0.50]:
        name = f"noisy_days_overdue_{int(frac*100)}pct_std"
        p_df = perturb_numeric_noise(test_df, "days_overdue", frac, rng_seed=42, clip_min=0.0)
        perturbation_results[name] = {
            "type": "numeric_noise",
            "column": "days_overdue",
            "noise_std_fraction": frac,
            "results": evaluate_policies_on_slice(test_df, p_df, model),
        }

    # 3e. Multi-feature combined perturbation (telemetry degradation)
    p_combined = test_df.copy()
    p_combined = perturb_categorical_missingness(p_combined, "failure_type", 0.20, rng_seed=42)
    p_combined = perturb_categorical_missingness(p_combined, "payment_method", 0.20, rng_seed=43)
    p_combined = perturb_numeric_noise(p_combined, "amount", 0.20, rng_seed=44, clip_min=10.0)
    p_combined = perturb_numeric_noise(p_combined, "days_overdue", 0.20, rng_seed=45, clip_min=0.0)
    perturbation_results["multi_feature_telemetry_degradation"] = {
        "type": "multi_feature_combined",
        "description": "20% missing failure_type + 20% missing payment_method + 20% noise on amount & days_overdue",
        "results": evaluate_policies_on_slice(test_df, p_combined, model),
    }

    results["feature_perturbation_experiments"] = perturbation_results

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Rare Combination / Stress Tests
    # ─────────────────────────────────────────────────────────────────────────
    rare_masks = []
    for ft, pm in RARE_COMBOS:
        rare_masks.append((test_df["failure_type"] == ft) & (test_df["payment_method"] == pm))
    
    combined_rare_mask = pd.concat(rare_masks, axis=1).any(axis=1)
    rare_df = test_df[combined_rare_mask].copy()

    # Extreme stress slice: high attempt (>= 4) AND high contact (>= 4) AND days_overdue >= 10
    extreme_mask = (test_df["attempt_number"] >= 4) | ((test_df["contact_count"] >= 5) & (test_df["days_overdue"] >= 10))
    extreme_df = test_df[extreme_mask].copy()

    results["stress_test_experiments"] = {
        "rare_combinations_pool": {
            "n": len(rare_df),
            "combos": [f"{ft}+{pm}" for ft, pm in RARE_COMBOS],
            "results": evaluate_policies_on_slice(rare_df, rare_df, model),
        },
        "extreme_debt_fatigue_cases": {
            "n": len(extreme_df),
            "description": "attempt >= 4 OR (contact >= 5 AND days_overdue >= 10)",
            "results": evaluate_policies_on_slice(extreme_df, extreme_df, model),
        },
    }

    return results


if __name__ == "__main__":
    res = run_all_robustness_experiments()
    print("Multi-seed runs summary:", json.dumps(res["multi_seed_experiments"]["summary"], indent=2))
    print("Distribution slices count:", len(res["distribution_slice_experiments"]))
    print("Perturbations count:", len(res["feature_perturbation_experiments"]))
    print("Stress tests count:", len(res["stress_test_experiments"]))
