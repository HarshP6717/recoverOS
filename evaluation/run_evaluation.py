"""
RecoverOS Phase 2C — Evaluation Runner.

Entry point: loads data + model, runs both evaluations, computes metrics,
produces comparison, writes JSON + Markdown report.

Usage:
    python evaluation/run_evaluation.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Enable UTF-8 for Windows console
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from evaluation.evaluator import load_and_evaluate
from evaluation.metrics import compute_policy_metrics
from evaluation.comparison import compute_comparison
from evaluation.report import save_json_results, generate_markdown_report, save_markdown_report


def main() -> None:
    print("=" * 72)
    print("RecoverOS Phase 2C — Isolated Evaluation Engine")
    print("=" * 72)

    # ── Step 1: Run both evaluations ──────────────────────────────────────────
    print("\n[1/4] Running evaluations on 1,000 held-out test records ...")
    result = load_and_evaluate()
    test_df = result["test_df"]
    baseline_records = result["baseline_records"]
    recoveros_records = result["recoveros_records"]
    n = len(test_df)
    print(f"      Evaluated {n} records for each policy (seed = 42 + index).")

    # ── Step 2: Compute metrics ───────────────────────────────────────────────
    print("\n[2/4] Computing metrics ...")
    baseline_metrics = compute_policy_metrics(
        baseline_records, "Deterministic Baseline"
    )
    recoveros_metrics = compute_policy_metrics(
        recoveros_records, "RecoverOS ML Policy (Expected Value + Guardrails)"
    )

    # ── Step 3: Compute comparison ────────────────────────────────────────────
    print("[3/4] Computing comparison ...")
    comparison = compute_comparison(baseline_metrics, recoveros_metrics)

    # ── Step 4: Save results ──────────────────────────────────────────────────
    print("[4/4] Writing JSON and Markdown results ...")
    paths = save_json_results(baseline_metrics, recoveros_metrics, comparison)
    report_text = generate_markdown_report(
        baseline_metrics,
        recoveros_metrics,
        comparison,
        n_test_cases=n,
        seed_formula="seed = 42 + index",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    report_path = save_markdown_report(report_text)

    # ── Console summary ───────────────────────────────────────────────────────
    b = baseline_metrics
    r = recoveros_metrics
    diff = comparison["absolute_difference"]
    uplift = comparison["relative_uplift_pct"]

    print("\n" + "=" * 72)
    print("RESULTS SUMMARY")
    print("=" * 72)

    print(f"\n{'Metric':<35} {'Baseline':>14} {'RecoverOS':>14} {'Δ':>14}")
    print("-" * 80)

    def row(label, bv, rv, dv, fmt=".4f"):
        print(f"{label:<35} {bv:>14{fmt}} {rv:>14{fmt}} {dv:>+14{fmt}}")

    row("Recovery Rate", b["recovery_rate"], r["recovery_rate"],
        diff["recovery_rate"])
    row("Recovered Count", b["recovered_count"], r["recovered_count"],
        diff["recovered_count"], ".0f")
    row("Total Recovered (INR)", b["total_recovered_amount"],
        r["total_recovered_amount"], diff["total_recovered_amount"], ".2f")
    row("Total Action Cost (INR)", b["total_action_cost"],
        r["total_action_cost"], diff["total_action_cost"], ".2f")
    row("Total Net Value (INR)", b["total_net_value"],
        r["total_net_value"], diff["total_net_value"], ".2f")
    row("Avg Net Value / Case (INR)", b["avg_net_value_per_case"],
        r["avg_net_value_per_case"], diff["avg_net_value_per_case"], ".4f")
    row("Stop Rate", b["stop_rate"], r["stop_rate"],
        diff["stop_rate"])

    print("\n" + "-" * 72)
    print("RELATIVE UPLIFT (RecoverOS vs Baseline)")
    print("-" * 72)
    for field in ["total_net_value", "recovery_rate", "total_recovered_amount",
                  "total_action_cost", "avg_net_value_per_case"]:
        u = uplift.get(field)
        u_str = f"{u:+.4f}%" if u is not None else "N/A (baseline=0)"
        print(f"  {field:<35}: {u_str}")

    print("\n" + "-" * 72)
    print("ACTION DISTRIBUTION")
    print("-" * 72)
    from simulator.recovery_simulator import ACTIONS
    print(f"  {'Action':<25} {'Baseline':>10} {'RecoverOS':>10} {'Δ':>8}")
    for action in ACTIONS:
        bc = b["action_distribution"].get(action, 0)
        rc = r["action_distribution"].get(action, 0)
        print(f"  {action:<25} {bc:>10} {rc:>10} {rc-bc:>+8}")

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(f"  {comparison['verdict']}")

    print("\n" + "=" * 72)
    print("OUTPUT FILES")
    print("=" * 72)
    for k, p in paths.items():
        print(f"  {k:<15}: {p}")
    print(f"  {'report':<15}: {report_path}")
    print()


if __name__ == "__main__":
    main()
