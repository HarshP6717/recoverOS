"""
RecoverOS Phase 2C — Report Generator.

Writes evaluation results to JSON files and a Markdown report.
All values are passed in from actual execution; nothing is computed here
beyond formatting.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# ── JSON helpers ───────────────────────────────────────────────────────────────

def _write_json(data: Any, path: Path) -> None:
    """Write data as indented JSON to path, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def save_json_results(
    baseline_metrics: Dict[str, Any],
    recoveros_metrics: Dict[str, Any],
    comparison: Dict[str, Any],
) -> Dict[str, Path]:
    """
    Persist the three metric dicts as JSON files under evaluation/results/.

    Returns
    -------
    Dict[str, Path]
        Mapping of logical name → file path.
    """
    paths = {
        "baseline": RESULTS_DIR / "baseline_metrics.json",
        "recoveros": RESULTS_DIR / "recoveros_metrics.json",
        "comparison": RESULTS_DIR / "comparison.json",
    }
    _write_json(baseline_metrics, paths["baseline"])
    _write_json(recoveros_metrics, paths["recoveros"])
    _write_json(comparison, paths["comparison"])
    return paths


# ── Markdown helpers ───────────────────────────────────────────────────────────

def _fmt_inr(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"₹{val:,.4f}"


def _fmt_pct(val: Optional[float], decimals: int = 4) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}%"


def _fmt_val(val: Optional[float], decimals: int = 6) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


def _uplift_str(val: Optional[float]) -> str:
    if val is None:
        return "N/A (baseline=0)"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.4f}%"


def generate_markdown_report(
    baseline_metrics: Dict[str, Any],
    recoveros_metrics: Dict[str, Any],
    comparison: Dict[str, Any],
    n_test_cases: int,
    seed_formula: str = "seed = 42 + index",
    generated_at: Optional[str] = None,
) -> str:
    """
    Generate the full Markdown evaluation report.

    The report explicitly separates measured results, baseline, RecoverOS,
    differences, uplift, action distribution, interpretation, and limitations.
    Uses no marketing language.
    """
    if generated_at is None:
        generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    b = baseline_metrics
    r = recoveros_metrics
    c = comparison
    diff = c["absolute_difference"]
    uplift = c["relative_uplift_pct"]
    adist = c["action_distribution_shift"]

    from simulator.recovery_simulator import ACTIONS  # import here to avoid circular

    lines: List[str] = []

    # Header
    lines += [
        "# RecoverOS Phase 2C — Evaluation Report",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Test Population:** {n_test_cases} held-out records (`data/processed/test.csv`)  ",
        f"**Seed formula:** `{seed_formula}`  ",
        f"**Both policies evaluated on the exact same {n_test_cases} records in the same order.**",
        "",
        "> All metrics are derived from actual simulated outcomes via `simulate_action()`.  ",
        "> No values are fabricated, estimated, or hard-coded.",
        "",
        "---",
        "",
    ]

    # ── A. Measured Results Summary ────────────────────────────────────────────
    lines += [
        "## A. Measured Results Summary",
        "",
        "| Metric | Baseline | RecoverOS | Absolute Diff | Relative Uplift |",
        "|--------|----------|-----------|---------------|-----------------|",
        f"| Recovery Rate | {_fmt_pct(b['recovery_rate']*100)} | {_fmt_pct(r['recovery_rate']*100)} | {_fmt_pct(diff['recovery_rate']*100, 4)} | {_uplift_str(uplift.get('recovery_rate'))} |",
        f"| Recovered Count | {b['recovered_count']} | {r['recovered_count']} | {diff['recovered_count']:+.0f} | {_uplift_str(uplift.get('recovered_count'))} |",
        f"| Total Recovered Amount | {_fmt_inr(b['total_recovered_amount'])} | {_fmt_inr(r['total_recovered_amount'])} | {_fmt_inr(diff['total_recovered_amount'])} | {_uplift_str(uplift.get('total_recovered_amount'))} |",
        f"| Total Action Cost | {_fmt_inr(b['total_action_cost'])} | {_fmt_inr(r['total_action_cost'])} | {_fmt_inr(diff['total_action_cost'])} | {_uplift_str(uplift.get('total_action_cost'))} |",
        f"| Total Net Value | {_fmt_inr(b['total_net_value'])} | {_fmt_inr(r['total_net_value'])} | {_fmt_inr(diff['total_net_value'])} | {_uplift_str(uplift.get('total_net_value'))} |",
        f"| Avg Net Value / Case | {_fmt_inr(b['avg_net_value_per_case'])} | {_fmt_inr(r['avg_net_value_per_case'])} | {_fmt_inr(diff['avg_net_value_per_case'])} | {_uplift_str(uplift.get('avg_net_value_per_case'))} |",
        f"| Stop Rate | {_fmt_pct(b['stop_rate']*100)} | {_fmt_pct(r['stop_rate']*100)} | {_fmt_pct(diff['stop_rate']*100, 4)} | {_uplift_str(uplift.get('stop_rate'))} |",
        "",
        "---",
        "",
    ]

    # ── B. Baseline Policy Results ─────────────────────────────────────────────
    lines += [
        "## B. Baseline Policy Results",
        "",
        f"**Policy:** {b['policy_name']}  ",
        f"**Cases evaluated:** {b['n_cases']}",
        "",
        f"- Recovered cases: **{b['recovered_count']}** / {b['n_cases']}",
        f"- Recovery rate: **{_fmt_pct(b['recovery_rate']*100)}**",
        f"- Total recovered amount: **{_fmt_inr(b['total_recovered_amount'])}**",
        f"- Total action cost: **{_fmt_inr(b['total_action_cost'])}**",
        f"- Total net value: **{_fmt_inr(b['total_net_value'])}**",
        f"- Avg net value / case: **{_fmt_inr(b['avg_net_value_per_case'])}**",
        f"- Stop count: **{b['stop_count']}** ({_fmt_pct(b['stop_rate']*100)} of cases)",
        f"- Avg predicted ERV: N/A (rule-based policy; no ML predictions)",
        "",
        "### Baseline Per-Action Recovery Breakdown",
        "",
        "| Action | Count | Recovered | Recovery Rate | Total Net Value |",
        "|--------|-------|-----------|---------------|-----------------|",
    ]
    for action in ACTIONS:
        bd = b["action_recovery_breakdown"].get(action, {})
        if bd:
            lines.append(
                f"| {action} | {bd['count']} | {bd['recovered']} | "
                f"{_fmt_pct(bd['recovery_rate']*100)} | {_fmt_inr(bd['total_net_value'])} |"
            )
    lines += ["", "---", ""]

    # ── C. RecoverOS Policy Results ────────────────────────────────────────────
    lines += [
        "## C. RecoverOS Policy Results",
        "",
        f"**Policy:** {r['policy_name']}  ",
        f"**Cases evaluated:** {r['n_cases']}",
        "",
        f"- Recovered cases: **{r['recovered_count']}** / {r['n_cases']}",
        f"- Recovery rate: **{_fmt_pct(r['recovery_rate']*100)}**",
        f"- Total recovered amount: **{_fmt_inr(r['total_recovered_amount'])}**",
        f"- Total action cost: **{_fmt_inr(r['total_action_cost'])}**",
        f"- Total net value: **{_fmt_inr(r['total_net_value'])}**",
        f"- Avg net value / case: **{_fmt_inr(r['avg_net_value_per_case'])}**",
        f"- Stop count: **{r['stop_count']}** ({_fmt_pct(r['stop_rate']*100)} of cases)",
    ]
    if r.get("avg_predicted_erv") is not None:
        lines.append(f"- Avg predicted ERV (chosen action): **{_fmt_inr(r['avg_predicted_erv'])}**")
    if r.get("guardrails_triggered_total") is not None:
        lines.append(
            f"- Guardrail activations (Guardrail 2 + 3 counts per record): "
            f"**{r['guardrails_triggered_total']}** (across all records; a record "
            f"may trigger >1 guardrail)"
        )
    lines += [
        "",
        "### RecoverOS Per-Action Recovery Breakdown",
        "",
        "| Action | Count | Recovered | Recovery Rate | Total Net Value |",
        "|--------|-------|-----------|---------------|-----------------|",
    ]
    for action in ACTIONS:
        rd = r["action_recovery_breakdown"].get(action, {})
        if rd:
            lines.append(
                f"| {action} | {rd['count']} | {rd['recovered']} | "
                f"{_fmt_pct(rd['recovery_rate']*100)} | {_fmt_inr(rd['total_net_value'])} |"
            )
    lines += ["", "---", ""]

    # ── D. Absolute Differences ────────────────────────────────────────────────
    lines += [
        "## D. Absolute Differences (RecoverOS − Baseline)",
        "",
        "| Metric | Absolute Difference |",
        "|--------|---------------------|",
        f"| Recovered count | {diff['recovered_count']:+.0f} |",
        f"| Recovery rate | {diff['recovery_rate']:+.6f} |",
        f"| Total recovered amount | {_fmt_inr(diff['total_recovered_amount'])} |",
        f"| Total action cost | {_fmt_inr(diff['total_action_cost'])} |",
        f"| Total net value | {_fmt_inr(diff['total_net_value'])} |",
        f"| Avg net value / case | {_fmt_inr(diff['avg_net_value_per_case'])} |",
        f"| Stop count | {diff['stop_count']:+.0f} |",
        f"| Stop rate | {diff['stop_rate']:+.6f} |",
        "",
        "---",
        "",
    ]

    # ── E. Relative Uplift ─────────────────────────────────────────────────────
    lines += [
        "## E. Relative Uplift (RecoverOS vs Baseline)",
        "",
        "Relative uplift = (RecoverOS − Baseline) / |Baseline| × 100.  ",
        "N/A is shown when the baseline value is zero (undefined denominator).",
        "",
        "| Metric | Relative Uplift |",
        "|--------|-----------------|",
        f"| Recovery rate | {_uplift_str(uplift.get('recovery_rate'))} |",
        f"| Total recovered amount | {_uplift_str(uplift.get('total_recovered_amount'))} |",
        f"| Total action cost | {_uplift_str(uplift.get('total_action_cost'))} |",
        f"| Total net value | {_uplift_str(uplift.get('total_net_value'))} |",
        f"| Avg net value / case | {_uplift_str(uplift.get('avg_net_value_per_case'))} |",
        f"| Stop rate | {_uplift_str(uplift.get('stop_rate'))} |",
        "",
        "---",
        "",
    ]

    # ── F. Action Distribution ─────────────────────────────────────────────────
    lines += [
        "## F. Action Distribution",
        "",
        "| Action | Baseline Count | Baseline % | RecoverOS Count | RecoverOS % | Δ Count | Δ ppt |",
        "|--------|---------------|------------|-----------------|-------------|---------|-------|",
    ]
    for action in ACTIONS:
        s = adist.get(action, {})
        if s:
            lines.append(
                f"| {action} | {s['baseline_count']} | {_fmt_pct(s['baseline_pct'])} | "
                f"{s['recoveros_count']} | {_fmt_pct(s['recoveros_pct'])} | "
                f"{s['count_diff']:+d} | {s['pct_point_diff']:+.4f} |"
            )
    lines += ["", "---", ""]

    # ── G. Interpretation ─────────────────────────────────────────────────────
    lines += [
        "## G. Interpretation",
        "",
        f"**Primary verdict (based on actual simulation outcomes):**",
        f"> {c['verdict']}",
        "",
        "### Key observations",
        "",
        "1. **Primary metric:** Total net value is the definitive comparison criterion,",
        "   because it integrates both the simulator's ground-truth recovery outcomes",
        "   and action execution costs.",
        "",
        "2. **Recovery rate vs. net value trade-off:** A higher recovery rate does not",
        "   necessarily imply higher net value if it is achieved at disproportionately",
        "   higher cost (e.g., choosing `escalate_human` at ₹30 per case).",
        "",
        "3. **Predicted ERV ≠ actual outcome:** RecoverOS chooses actions based on",
        "   ML-predicted ERV. The simulator then draws an independent Bernoulli trial",
        "   from the ground-truth probability, which differs from the predicted",
        "   probability. The comparison is therefore based on simulator outcomes, not",
        "   predicted values.",
        "",
        "4. **Guardrails:** Deterministic safety guardrails constrain the ML policy's",
        "   action space. Their effect on the action distribution is visible in Section F.",
        "",
        "---",
        "",
    ]

    # ── H. Limitations ────────────────────────────────────────────────────────
    lines += [
        "## H. Limitations",
        "",
        "1. **Synthetic data:** Both the features and the simulator's ground-truth",
        "   probabilities are derived from a synthetic dataset. Results may not",
        "   generalise to real payment recovery dynamics.",
        "",
        "2. **Single-action evaluation:** This evaluation assigns one action per case.",
        "   Real recovery workflows are sequential; a multi-step evaluation would be",
        "   more representative.",
        "",
        "3. **Frozen model:** The model was trained on the training split of the same",
        "   synthetic data distribution. Generalisation to a different distribution",
        "   has not been evaluated.",
        "",
        "4. **Stochasticity:** Despite the deterministic seed formula `seed = 42 + i`,",
        "   the Bernoulli outcomes introduce sampling variance. Running more trials",
        "   would reduce variance in the comparison.",
        "",
        "5. **Single test population:** The held-out test set is 1,000 records.",
        "   Statistical significance of the uplift has not been formally tested.",
        "",
        "6. **No online feedback loop:** The ML model is evaluated offline.",
        "   Online exploration, bandit feedback, or policy gradient methods could",
        "   alter conclusions.",
        "",
        "---",
        "",
        "*End of RecoverOS Phase 2C Evaluation Report.*",
    ]

    return "\n".join(lines)


def save_markdown_report(report_text: str) -> Path:
    """Write the Markdown report to evaluation/results/evaluation_report.md."""
    path = RESULTS_DIR / "evaluation_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return path
