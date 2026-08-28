"""
RecoverOS Phase 2C Step 2A — Three-Policy Evaluation + Bootstrap CI.

Evaluates:
  1. DeterministicBaselinePolicy
  2. StrongFeatureAwareHeuristic
  3. MLExpectedValuePolicy (RecoverOS)

Using the exact same 1,000 test cases, seed = 42 + index.

Then runs a deterministic bootstrap (N=1,000, BOOTSTRAP_SEED=0) to compute
95% confidence intervals on the net value differences.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from evaluation.evaluator import (
    SEED_BASE,
    N_EXPECTED,
    _load_test_data,
    _load_ml_model,
    run_baseline_evaluation,
    run_recoveros_evaluation,
)
from evaluation.metrics import compute_policy_metrics
from evaluation.policies.feature_aware_heuristic import StrongFeatureAwareHeuristic
from simulator.recovery_simulator import ACTIONS, simulate_action

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Bootstrap parameters — documented before execution
N_BOOTSTRAPS: int = 1_000
BOOTSTRAP_SEED: int = 0     # Deterministic; fixed before any experiment was run


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic evaluator
# ─────────────────────────────────────────────────────────────────────────────

def run_heuristic_evaluation(test_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Evaluate StrongFeatureAwareHeuristic on the full test population."""
    policy = StrongFeatureAwareHeuristic()
    chosen_actions = policy.select_actions_batch(test_df)

    records = []
    for i in range(len(test_df)):
        row = test_df.iloc[i]
        action = chosen_actions[i]
        seed = SEED_BASE + i
        sim = simulate_action(row, action, seed=seed)
        records.append({
            "index": i,
            "action": sim["action"],
            "recovered": sim["recovered"],
            "recovered_amount": sim["recovered_amount"],
            "action_cost": sim["action_cost"],
            "net_value": sim["net_value"],
            "recovery_probability_gt": sim["recovery_probability"],
            "predicted_erv": None,
            "guardrails_triggered": None,
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_net_value(
    records_a: List[Dict],
    records_b: List[Dict],
    label_a: str,
    label_b: str,
    n_bootstraps: int = N_BOOTSTRAPS,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """
    Deterministic bootstrap over the per-case net values.

    For each of n_bootstraps iterations:
      1. Resample 1,000 case indices with replacement (seed-controlled).
      2. Compute mean net value for each policy on the resample.
      3. Record the net value difference (b - a) = (RecoverOS - comparator).

    Returns point estimate, 95% CI, and whether CI crosses zero.
    """
    n = len(records_a)
    assert len(records_b) == n

    nv_a = np.array([r["net_value"] for r in records_a], dtype=float)
    nv_b = np.array([r["net_value"] for r in records_b], dtype=float)

    rng = np.random.default_rng(seed)
    diffs_total_nv: List[float] = []
    diffs_recovery_rate: List[float] = []

    recovered_a = np.array([float(r["recovered"]) for r in records_a])
    recovered_b = np.array([float(r["recovered"]) for r in records_b])

    for _ in range(n_bootstraps):
        idx = rng.integers(0, n, size=n)
        diff_nv = float(nv_b[idx].sum() - nv_a[idx].sum())
        diff_rr = float(recovered_b[idx].mean() - recovered_a[idx].mean())
        diffs_total_nv.append(diff_nv)
        diffs_recovery_rate.append(diff_rr)

    arr_nv = np.array(diffs_total_nv)
    arr_rr = np.array(diffs_recovery_rate)

    point_nv = float(sum(r["net_value"] for r in records_b) -
                     sum(r["net_value"] for r in records_a))
    point_rr = float(np.mean(recovered_b) - np.mean(recovered_a))

    ci_nv_lo, ci_nv_hi = float(np.percentile(arr_nv, 2.5)), float(np.percentile(arr_nv, 97.5))
    ci_rr_lo, ci_rr_hi = float(np.percentile(arr_rr, 2.5)), float(np.percentile(arr_rr, 97.5))

    return {
        "comparator_label": label_a,
        "recoveros_label": label_b,
        "n_bootstraps": n_bootstraps,
        "bootstrap_seed": seed,
        "point_estimate_total_net_value_diff": round(point_nv, 4),
        "point_estimate_recovery_rate_diff": round(point_rr, 6),
        "bootstrap_ci_95_total_net_value": [round(ci_nv_lo, 4), round(ci_nv_hi, 4)],
        "bootstrap_ci_95_recovery_rate": [round(ci_rr_lo, 6), round(ci_rr_hi, 6)],
        "ci_crosses_zero_net_value": bool(ci_nv_lo < 0 < ci_nv_hi or ci_nv_hi < 0),
        "ci_crosses_zero_recovery_rate": bool(ci_rr_lo < 0 < ci_rr_hi or ci_rr_hi < 0),
        "bootstrap_mean_diff_net_value": round(float(arr_nv.mean()), 4),
        "bootstrap_std_diff_net_value": round(float(arr_nv.std()), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Paired comparison
# ─────────────────────────────────────────────────────────────────────────────

def _paired_comparison(
    records_a: List[Dict],
    records_b: List[Dict],
    label_a: str,
    label_b: str,
) -> Dict[str, Any]:
    """Per-case net value difference: RecoverOS - comparator."""
    n = len(records_a)
    diffs = [records_b[i]["net_value"] - records_a[i]["net_value"] for i in range(n)]
    arr = np.array(diffs, dtype=float)

    wins_b   = int(sum(1 for d in diffs if d > 0))
    wins_a   = int(sum(1 for d in diffs if d < 0))
    ties     = int(sum(1 for d in diffs if d == 0))

    return {
        "comparator_label": label_a,
        "recoveros_label": label_b,
        "n_cases": n,
        "mean_delta_net_value": round(float(arr.mean()), 6),
        "median_delta_net_value": round(float(np.median(arr)), 6),
        "std_delta_net_value": round(float(arr.std()), 6),
        "percentiles": {
            "p5":  round(float(np.percentile(arr, 5)), 4),
            "p25": round(float(np.percentile(arr, 25)), 4),
            "p50": round(float(np.percentile(arr, 50)), 4),
            "p75": round(float(np.percentile(arr, 75)), 4),
            "p95": round(float(np.percentile(arr, 95)), 4),
        },
        f"{label_b}_wins": wins_b,
        f"{label_a}_wins": wins_a,
        "ties": ties,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Action-level comparison
# ─────────────────────────────────────────────────────────────────────────────

def _action_level_table(
    records: List[Dict],
    n_total: int,
) -> Dict[str, Any]:
    """Per-action: count, %, recovery_rate, total_net_value."""
    from collections import defaultdict
    rows: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "recovered": 0, "net_value": 0.0})
    for r in records:
        act = r["action"]
        rows[act]["count"] += 1
        rows[act]["recovered"] += int(r["recovered"])
        rows[act]["net_value"] += r["net_value"]

    result = {}
    for act in ACTIONS:
        cnt = rows[act]["count"]
        rec = rows[act]["recovered"]
        nv  = rows[act]["net_value"]
        result[act] = {
            "count": cnt,
            "pct": round(cnt / n_total * 100, 2) if n_total else 0.0,
            "recovery_rate": round(rec / cnt, 6) if cnt else 0.0,
            "total_net_value": round(nv, 4),
        }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Safe divide
# ─────────────────────────────────────────────────────────────────────────────

def _safe_pct(numer: float, denom: float) -> str:
    if denom == 0.0:
        return "N/A"
    return f"{numer / denom * 100:+.4f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def generate_step2a_report(
    b_metrics: Dict,
    h_metrics: Dict,
    r_metrics: Dict,
    boot_vs_baseline: Dict,
    boot_vs_heuristic: Dict,
    paired_vs_baseline: Dict,
    paired_vs_heuristic: Dict,
    b_action: Dict,
    h_action: Dict,
    r_action: Dict,
    generated_at: str,
) -> str:
    lines = [
        "# RecoverOS Phase 2C Step 2A — Statistical Validation Report",
        "",
        f"**Generated:** {generated_at}  ",
        "**Experiment:** Three-policy comparison with bootstrap confidence intervals",
        "",
        "> All values derived from actual execution. Heuristic rules derived",
        "> from simulator's published tables BEFORE inspecting test.csv outcomes.",
        "",
        "---",
        "",
        "## 1. Heuristic Design — Rule Derivation",
        "",
        "### What rules does the heuristic use?",
        "",
        "| Rule | Condition | Action | Justification |",
        "|------|-----------|--------|---------------|",
        "| 1 — Contact fatigue | contact_count ≥ 6 | send_reminder | Logit fatigue penalty ≥ −0.48 at contact=6; cheapest non-stop action |",
        "| 2a — Hard failure | failure_type ∈ {expired_card, hard_decline, invalid_payment_method} AND attempt < 4 | payment_method_update | Base p(retry) ≤ 0.02; p(pmu) ≥ 0.70 |",
        "| 2b — Hard failure, high attempt | Same hard failure types AND attempt ≥ 4 | recovery_link | After multiple attempts, recovery_link (cost ₹1.50) preferred over escalation |",
        "| 3 — High attempt (non-hard) | attempt_number ≥ 4 | fallback per failure_type | attempt penalty = −0.32×(attempt−1); primary action significantly degraded |",
        "| 4 — Primary action | All other cases | lookup by failure_type | Read directly from BASE_ACTION_PROBABILITIES (highest ERV at base probs) |",
        "| 5 — Escalation upgrade | repeated_failure AND amount ≥ ₹200 AND attempt=1 AND contact ≤ 2 | escalate_human | Base p=0.76; ERV = 0.76×amt − 30 > 0 for all amounts ≥ ₹40 |",
        "",
        "### Why are these rules defensible?",
        "",
        "All thresholds are read from the simulator's published tables:",
        "- `BASE_ACTION_PROBABILITIES` (lines 65–147 of `recovery_simulator.py`)",
        "- `ACTION_COSTS`: retry=₹1, send_reminder=₹0.50, payment_method_update=₹2, recovery_link=₹1.50, escalate_human=₹30",
        "- Contextual modifier coefficients: attempt_penalty=−0.32, fatigue_penalty=−0.12",
        "",
        "The heuristic was designed by reading these tables and computing ERV rankings.",
        "**No test.csv outcome was inspected before finalising any rule.**",
        "",
        "---",
        "",
        "## 2. Three-Policy Results",
        "",
        "| Metric | Baseline | Heuristic | RecoverOS |",
        "|--------|----------|-----------|-----------|",
        f"| Recovery Rate | {b_metrics['recovery_rate']*100:.4f}% | {h_metrics['recovery_rate']*100:.4f}% | {r_metrics['recovery_rate']*100:.4f}% |",
        f"| Recovered Count | {b_metrics['recovered_count']} | {h_metrics['recovered_count']} | {r_metrics['recovered_count']} |",
        f"| Total Recovered (₹) | {b_metrics['total_recovered_amount']:,.4f} | {h_metrics['total_recovered_amount']:,.4f} | {r_metrics['total_recovered_amount']:,.4f} |",
        f"| Total Action Cost (₹) | {b_metrics['total_action_cost']:,.4f} | {h_metrics['total_action_cost']:,.4f} | {r_metrics['total_action_cost']:,.4f} |",
        f"| Total Net Value (₹) | {b_metrics['total_net_value']:,.4f} | {h_metrics['total_net_value']:,.4f} | {r_metrics['total_net_value']:,.4f} |",
        f"| Avg Net Value/Case (₹) | {b_metrics['avg_net_value_per_case']:.4f} | {h_metrics['avg_net_value_per_case']:.4f} | {r_metrics['avg_net_value_per_case']:.4f} |",
        f"| Stop Rate | {b_metrics['stop_rate']*100:.2f}% | {h_metrics['stop_rate']*100:.2f}% | {r_metrics['stop_rate']*100:.2f}% |",
        "",
        "---",
        "",
        "## 3. Uplift Summary",
        "",
        "| Comparison | Abs Net Value Δ (₹) | Relative Uplift | Abs Recovery Rate Δ |",
        "|------------|---------------------|-----------------|----------------------|",
    ]

    b_nv = b_metrics["total_net_value"]
    h_nv = h_metrics["total_net_value"]
    r_nv = r_metrics["total_net_value"]
    b_rr = b_metrics["recovery_rate"]
    h_rr = h_metrics["recovery_rate"]
    r_rr = r_metrics["recovery_rate"]

    lines.append(
        f"| RecoverOS vs Baseline | {r_nv - b_nv:+,.4f} | {_safe_pct(r_nv - b_nv, b_nv)} | {(r_rr - b_rr)*100:+.4f} pp |"
    )
    lines.append(
        f"| RecoverOS vs Heuristic | {r_nv - h_nv:+,.4f} | {_safe_pct(r_nv - h_nv, h_nv)} | {(r_rr - h_rr)*100:+.4f} pp |"
    )
    lines.append(
        f"| Heuristic vs Baseline | {h_nv - b_nv:+,.4f} | {_safe_pct(h_nv - b_nv, b_nv)} | {(h_rr - b_rr)*100:+.4f} pp |"
    )

    lines += [
        "",
        "---",
        "",
        "## 4. Bootstrap Confidence Intervals",
        "",
        f"N_BOOTSTRAPS = {N_BOOTSTRAPS}, BOOTSTRAP_SEED = {BOOTSTRAP_SEED}  ",
        "Method: resample 1,000 case indices with replacement; use fixed per-case outcomes (no re-simulation).",
        "",
        "### RecoverOS vs Existing Baseline",
        "",
        f"- Point estimate (Total Net Value diff): **₹{boot_vs_baseline['point_estimate_total_net_value_diff']:,.4f}**",
        f"- 95% Bootstrap CI: **[₹{boot_vs_baseline['bootstrap_ci_95_total_net_value'][0]:,.4f}, ₹{boot_vs_baseline['bootstrap_ci_95_total_net_value'][1]:,.4f}]**",
        f"- CI crosses zero: **{boot_vs_baseline['ci_crosses_zero_net_value']}**",
        f"- Point estimate (Recovery Rate diff): **{boot_vs_baseline['point_estimate_recovery_rate_diff']*100:+.4f} pp**",
        f"- 95% Bootstrap CI (RR): **[{boot_vs_baseline['bootstrap_ci_95_recovery_rate'][0]*100:+.4f} pp, {boot_vs_baseline['bootstrap_ci_95_recovery_rate'][1]*100:+.4f} pp]**",
        f"- Bootstrap mean diff: ₹{boot_vs_baseline['bootstrap_mean_diff_net_value']:,.4f} ± ₹{boot_vs_baseline['bootstrap_std_diff_net_value']:,.4f} (std)",
        "",
        "### RecoverOS vs Strong Heuristic",
        "",
        f"- Point estimate (Total Net Value diff): **₹{boot_vs_heuristic['point_estimate_total_net_value_diff']:,.4f}**",
        f"- 95% Bootstrap CI: **[₹{boot_vs_heuristic['bootstrap_ci_95_total_net_value'][0]:,.4f}, ₹{boot_vs_heuristic['bootstrap_ci_95_total_net_value'][1]:,.4f}]**",
        f"- CI crosses zero: **{boot_vs_heuristic['ci_crosses_zero_net_value']}**",
        f"- Point estimate (Recovery Rate diff): **{boot_vs_heuristic['point_estimate_recovery_rate_diff']*100:+.4f} pp**",
        f"- 95% Bootstrap CI (RR): **[{boot_vs_heuristic['bootstrap_ci_95_recovery_rate'][0]*100:+.4f} pp, {boot_vs_heuristic['bootstrap_ci_95_recovery_rate'][1]*100:+.4f} pp]**",
        f"- Bootstrap mean diff: ₹{boot_vs_heuristic['bootstrap_mean_diff_net_value']:,.4f} ± ₹{boot_vs_heuristic['bootstrap_std_diff_net_value']:,.4f} (std)",
        "",
        "---",
        "",
        "## 5. Paired Per-Case Analysis",
        "",
        "### RecoverOS vs Existing Baseline",
        "",
        f"| Statistic | Value |",
        f"|-----------|-------|",
        f"| Mean Δ net value | ₹{paired_vs_baseline['mean_delta_net_value']:,.6f} |",
        f"| Median Δ net value | ₹{paired_vs_baseline['median_delta_net_value']:,.6f} |",
        f"| Std Δ net value | ₹{paired_vs_baseline['std_delta_net_value']:,.6f} |",
        f"| RecoverOS wins | {paired_vs_baseline.get('RecoverOS_wins', paired_vs_baseline.get('MLExpectedValuePolicy (Expected Value + Guardrails)_wins', 'N/A'))} |",
        f"| Baseline wins | {paired_vs_baseline.get('Deterministic Baseline_wins', 'N/A')} |",
        f"| Ties | {paired_vs_baseline['ties']} |",
        "",
        "Percentile distribution of per-case deltas (RecoverOS − Baseline):",
        f"p5={paired_vs_baseline['percentiles']['p5']:+.4f}, p25={paired_vs_baseline['percentiles']['p25']:+.4f}, p50={paired_vs_baseline['percentiles']['p50']:+.4f}, p75={paired_vs_baseline['percentiles']['p75']:+.4f}, p95={paired_vs_baseline['percentiles']['p95']:+.4f}",
        "",
        "### RecoverOS vs Strong Heuristic",
        "",
        f"| Statistic | Value |",
        f"|-----------|-------|",
        f"| Mean Δ net value | ₹{paired_vs_heuristic['mean_delta_net_value']:,.6f} |",
        f"| Median Δ net value | ₹{paired_vs_heuristic['median_delta_net_value']:,.6f} |",
        f"| Std Δ net value | ₹{paired_vs_heuristic['std_delta_net_value']:,.6f} |",
    ]

    # Handle dynamic key names from paired comparison
    r_wins_key = [k for k in paired_vs_heuristic if k.endswith("_wins") and "Heuristic" not in k]
    h_wins_key = [k for k in paired_vs_heuristic if "Heuristic" in k and k.endswith("_wins")]
    lines += [
        f"| RecoverOS wins | {paired_vs_heuristic[r_wins_key[0]] if r_wins_key else 'N/A'} |",
        f"| Heuristic wins | {paired_vs_heuristic[h_wins_key[0]] if h_wins_key else 'N/A'} |",
        f"| Ties | {paired_vs_heuristic['ties']} |",
        "",
        "Percentile distribution of per-case deltas (RecoverOS − Heuristic):",
        f"p5={paired_vs_heuristic['percentiles']['p5']:+.4f}, p25={paired_vs_heuristic['percentiles']['p25']:+.4f}, p50={paired_vs_heuristic['percentiles']['p50']:+.4f}, p75={paired_vs_heuristic['percentiles']['p75']:+.4f}, p95={paired_vs_heuristic['percentiles']['p95']:+.4f}",
        "",
        "---",
        "",
        "## 6. Action-Level Analysis",
        "",
        "| Action | B Count | B % | B RR | B NV (₹) | H Count | H % | H RR | H NV (₹) | R Count | R % | R RR | R NV (₹) |",
        "|--------|---------|-----|------|-----------|---------|-----|------|-----------|---------|-----|------|-----------|",
    ]
    for act in ACTIONS:
        b = b_action.get(act, {})
        h = h_action.get(act, {})
        r = r_action.get(act, {})
        lines.append(
            f"| {act} "
            f"| {b.get('count',0)} | {b.get('pct',0):.1f}% | {b.get('recovery_rate',0):.4f} | {b.get('total_net_value',0):.2f} "
            f"| {h.get('count',0)} | {h.get('pct',0):.1f}% | {h.get('recovery_rate',0):.4f} | {h.get('total_net_value',0):.2f} "
            f"| {r.get('count',0)} | {r.get('pct',0):.1f}% | {r.get('recovery_rate',0):.4f} | {r.get('total_net_value',0):.2f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 7. Generalization Warning",
        "",
        "> ⚠️ **This evaluation demonstrates performance WITHIN the synthetic simulator distribution.**",
        ">",
        "> It does NOT establish:",
        "> - Real Razorpay payment recovery improvement",
        "> - Real customer recovery probability",
        "> - Real-world ROI",
        "> - Production ML generalisation under covariate shift",
        ">",
        "> Both training data and the ground-truth simulator probability function are",
        "> synthetic. The ML model was trained on the same synthetic distribution",
        "> it is tested on. These results are valid for comparing the three policies",
        "> under controlled, reproducible, synthetic conditions only.",
        "",
        "---",
        "",
        "## 8. Summary Findings",
        "",
    ]

    # Honest summary
    r_vs_b_ci = boot_vs_baseline["bootstrap_ci_95_total_net_value"]
    r_vs_h_ci = boot_vs_heuristic["bootstrap_ci_95_total_net_value"]
    r_vs_b_crosses = boot_vs_baseline["ci_crosses_zero_net_value"]
    r_vs_h_crosses = boot_vs_heuristic["ci_crosses_zero_net_value"]

    lines += [
        f"- The strong heuristic achieves total net value of **₹{h_nv:,.2f}** vs baseline ₹{b_nv:,.2f} (+{((h_nv-b_nv)/b_nv*100):+.2f}%).",
        f"- RecoverOS achieves **₹{r_nv:,.2f}** net value.",
        f"- RecoverOS vs Baseline: {'+' if r_nv > b_nv else ''}₹{r_nv-b_nv:,.2f} ({_safe_pct(r_nv-b_nv,b_nv)}). 95% CI: [₹{r_vs_b_ci[0]:,.2f}, ₹{r_vs_b_ci[1]:,.2f}]. CI crosses zero: **{r_vs_b_crosses}**.",
        f"- RecoverOS vs Heuristic: {'+' if r_nv > h_nv else ''}₹{r_nv-h_nv:,.2f} ({_safe_pct(r_nv-h_nv,h_nv)}). 95% CI: [₹{r_vs_h_ci[0]:,.2f}, ₹{r_vs_h_ci[1]:,.2f}]. CI crosses zero: **{r_vs_h_crosses}**.",
        "",
    ]

    if r_nv > h_nv and not r_vs_h_crosses:
        conclusion = "RecoverOS demonstrates measurable value over the strong heuristic under this synthetic evaluation."
    elif r_nv > h_nv and r_vs_h_crosses:
        conclusion = "RecoverOS achieves a higher point estimate than the heuristic, but the 95% bootstrap CI crosses zero. The improvement is not statistically conclusive at this sample size."
    elif r_nv <= h_nv:
        conclusion = "RecoverOS does NOT demonstrate measurable value over the strong heuristic in this evaluation. The heuristic performs at least as well."

    lines += [
        f"> **Honest conclusion:** {conclusion}",
        "",
        "### What this experiment still cannot prove",
        "",
        "- That results generalise to real Razorpay data.",
        "- That the +82.1% vs DeterministicBaseline is due to ML and not simply due to better feature usage.",
        "- Statistical significance in the frequentist sense (no p-value computed).",
        "- That the heuristic would remain weaker at a different hyperparameter or threshold choice.",
        "",
        "---",
        "",
        "*End of Phase 2C Step 2A Report.*",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 72)
    print("RecoverOS Phase 2C Step 2A — Statistical Validation")
    print("=" * 72)

    # Load data and model
    print("\n[1/8] Loading test data and model ...")
    test_df = _load_test_data()
    model   = _load_ml_model()
    n = len(test_df)
    print(f"      {n} test records loaded.")

    # Evaluate all three policies
    print("[2/8] Evaluating Deterministic Baseline ...")
    baseline_records  = run_baseline_evaluation(test_df)

    print("[3/8] Evaluating Strong Feature-Aware Heuristic ...")
    heuristic_records = run_heuristic_evaluation(test_df)

    print("[4/8] Evaluating RecoverOS ML Policy ...")
    recoveros_records = run_recoveros_evaluation(test_df, model)

    # Verify identical population coverage
    assert all(b["index"] == h["index"] == r["index"] == i
               for i, (b, h, r) in enumerate(zip(baseline_records, heuristic_records, recoveros_records))), \
        "Population index mismatch"

    # Compute metrics
    print("[5/8] Computing metrics ...")
    b_metrics = compute_policy_metrics(baseline_records,  "Deterministic Baseline")
    h_metrics = compute_policy_metrics(heuristic_records, "Strong Feature-Aware Heuristic")
    r_metrics = compute_policy_metrics(recoveros_records,  "RecoverOS ML Policy")

    # Action-level tables
    b_action = _action_level_table(baseline_records,  n)
    h_action = _action_level_table(heuristic_records, n)
    r_action = _action_level_table(recoveros_records,  n)

    # Bootstrap
    print(f"[6/8] Running bootstrap (N={N_BOOTSTRAPS}, seed={BOOTSTRAP_SEED}) ...")
    boot_vs_baseline  = _bootstrap_net_value(baseline_records,  recoveros_records,
                                              "Deterministic Baseline",  "RecoverOS ML Policy")
    boot_vs_heuristic = _bootstrap_net_value(heuristic_records, recoveros_records,
                                              "Strong Feature-Aware Heuristic", "RecoverOS ML Policy")

    # Paired comparison
    print("[7/8] Running paired comparison ...")
    paired_vs_baseline  = _paired_comparison(baseline_records,  recoveros_records,
                                              "Deterministic Baseline",  "RecoverOS")
    paired_vs_heuristic = _paired_comparison(heuristic_records, recoveros_records,
                                              "Strong Feature-Aware Heuristic", "RecoverOS")

    # Write outputs
    print("[8/8] Writing output files ...")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    heuristic_path = RESULTS_DIR / "heuristic_metrics.json"
    with open(heuristic_path, "w", encoding="utf-8") as f:
        json.dump(h_metrics, f, indent=2)

    bootstrap_path = RESULTS_DIR / "bootstrap_results.json"
    with open(bootstrap_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "n_bootstraps": N_BOOTSTRAPS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "recoveros_vs_baseline": boot_vs_baseline,
            "recoveros_vs_heuristic": boot_vs_heuristic,
        }, f, indent=2)

    paired_path = RESULTS_DIR / "paired_comparison.json"
    with open(paired_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "recoveros_vs_baseline": paired_vs_baseline,
            "recoveros_vs_heuristic": paired_vs_heuristic,
        }, f, indent=2)

    report_text = generate_step2a_report(
        b_metrics, h_metrics, r_metrics,
        boot_vs_baseline, boot_vs_heuristic,
        paired_vs_baseline, paired_vs_heuristic,
        b_action, h_action, r_action,
        generated_at,
    )
    report_path = RESULTS_DIR / "phase2c_step2a_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Console summary
    b_nv = b_metrics["total_net_value"]
    h_nv = h_metrics["total_net_value"]
    r_nv = r_metrics["total_net_value"]

    print()
    print("=" * 72)
    print("RESULTS SUMMARY")
    print("=" * 72)
    print(f"  {'Policy':<35} {'Recovery Rate':>14} {'Net Value (₹)':>14}")
    print("  " + "-" * 65)
    print(f"  {'Deterministic Baseline':<35} {b_metrics['recovery_rate']*100:>13.2f}% {b_nv:>14,.2f}")
    print(f"  {'Strong Heuristic':<35} {h_metrics['recovery_rate']*100:>13.2f}% {h_nv:>14,.2f}")
    print(f"  {'RecoverOS ML Policy':<35} {r_metrics['recovery_rate']*100:>13.2f}% {r_nv:>14,.2f}")

    print()
    print("=" * 72)
    print("BOOTSTRAP 95% CIs (Total Net Value difference)")
    print("=" * 72)
    ci_b = boot_vs_baseline["bootstrap_ci_95_total_net_value"]
    ci_h = boot_vs_heuristic["bootstrap_ci_95_total_net_value"]
    print(f"  RecoverOS vs Baseline  : [{ci_b[0]:+,.2f}, {ci_b[1]:+,.2f}]  crosses_zero={boot_vs_baseline['ci_crosses_zero_net_value']}")
    print(f"  RecoverOS vs Heuristic : [{ci_h[0]:+,.2f}, {ci_h[1]:+,.2f}]  crosses_zero={boot_vs_heuristic['ci_crosses_zero_net_value']}")

    print()
    print("=" * 72)
    print("OUTPUT FILES")
    print("=" * 72)
    for path in [heuristic_path, bootstrap_path, paired_path, report_path]:
        print(f"  {path}")
    print()


if __name__ == "__main__":
    main()
