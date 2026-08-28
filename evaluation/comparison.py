"""
RecoverOS Phase 2C — Policy Comparison.

Computes uplift and difference metrics between baseline and RecoverOS policies.
All numbers originate from actual measured metrics; none are fabricated.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from evaluation.metrics import safe_divide


def compute_comparison(
    baseline_metrics: Dict[str, Any],
    recoveros_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute absolute differences and relative uplift between two policy metric dicts.

    Parameters
    ----------
    baseline_metrics : Dict[str, Any]
        Metrics produced by compute_policy_metrics() for DeterministicBaselinePolicy.
    recoveros_metrics : Dict[str, Any]
        Metrics produced by compute_policy_metrics() for MLExpectedValuePolicy.

    Returns
    -------
    Dict[str, Any]
        Comparison dict with absolute differences, relative uplifts, and
        action distribution shifts.
    """
    # ── Scalar field comparisons ───────────────────────────────────────────────
    scalar_fields = [
        "recovery_rate",
        "total_recovered_amount",
        "total_action_cost",
        "total_net_value",
        "avg_net_value_per_case",
        "stop_rate",
        "recovered_count",
        "stop_count",
    ]

    absolute_diff: Dict[str, float] = {}
    relative_uplift_pct: Dict[str, Optional[float]] = {}

    for field in scalar_fields:
        b_val = float(baseline_metrics.get(field, 0.0))
        r_val = float(recoveros_metrics.get(field, 0.0))
        diff = r_val - b_val
        absolute_diff[field] = round(diff, 6)
        # Relative uplift: (recoveros - baseline) / |baseline| * 100
        # Returns None (not 0.0) when denominator is zero to avoid misleading %
        if b_val == 0.0:
            relative_uplift_pct[field] = None
        else:
            relative_uplift_pct[field] = round((diff / abs(b_val)) * 100.0, 4)

    # ── Action distribution shift ──────────────────────────────────────────────
    b_dist: Dict[str, int] = baseline_metrics.get("action_distribution", {})
    r_dist: Dict[str, int] = recoveros_metrics.get("action_distribution", {})
    all_actions = sorted(set(list(b_dist.keys()) + list(r_dist.keys())))

    action_distribution_shift: Dict[str, Dict[str, Any]] = {}
    n = int(baseline_metrics.get("n_cases", 1))

    for action in all_actions:
        b_count = int(b_dist.get(action, 0))
        r_count = int(r_dist.get(action, 0))
        count_diff = r_count - b_count
        b_pct = round(safe_divide(b_count, n) * 100, 4)
        r_pct = round(safe_divide(r_count, n) * 100, 4)
        action_distribution_shift[action] = {
            "baseline_count": b_count,
            "recoveros_count": r_count,
            "count_diff": count_diff,
            "baseline_pct": b_pct,
            "recoveros_pct": r_pct,
            "pct_point_diff": round(r_pct - b_pct, 4),
        }

    # ── Primary verdict ────────────────────────────────────────────────────────
    # Based strictly on total_net_value from actual simulation outcomes.
    net_value_diff = absolute_diff.get("total_net_value", 0.0)
    if net_value_diff > 0.0:
        verdict = "RecoverOS exceeds baseline on total net value (actual simulation)."
    elif net_value_diff < 0.0:
        verdict = "RecoverOS underperforms baseline on total net value (actual simulation)."
    else:
        verdict = "RecoverOS and baseline are tied on total net value (actual simulation)."

    return {
        "n_cases": baseline_metrics.get("n_cases"),
        "baseline_policy_name": baseline_metrics.get("policy_name"),
        "recoveros_policy_name": recoveros_metrics.get("policy_name"),
        "absolute_difference": absolute_diff,
        "relative_uplift_pct": relative_uplift_pct,
        "action_distribution_shift": action_distribution_shift,
        "verdict": verdict,
    }
