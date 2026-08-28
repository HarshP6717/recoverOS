"""
RecoverOS Phase 2C — Metric Computation.

Computes summary metrics from per-case simulation records.
Every value originates from actual execution; no fabrication.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return numerator / denominator, or default when denominator is zero."""
    if denominator == 0.0:
        return default
    return numerator / denominator


def compute_policy_metrics(
    case_records: List[Dict[str, Any]],
    policy_name: str,
) -> Dict[str, Any]:
    """
    Aggregate per-case simulation records into policy-level metrics.

    Parameters
    ----------
    case_records : List[Dict[str, Any]]
        One dict per test case, each containing the keys produced by the
        evaluator:
            index, action, recovered, recovered_amount, action_cost,
            net_value, predicted_erv (optional), guardrails_triggered (optional)
    policy_name : str
        Human-readable name of the evaluated policy.

    Returns
    -------
    Dict[str, Any]
        All metrics derived exclusively from actual simulated outcomes.
    """
    if not case_records:
        return _empty_metrics(policy_name)

    n = len(case_records)

    recovered_flags: List[bool] = [r["recovered"] for r in case_records]
    recovered_amounts: List[float] = [r["recovered_amount"] for r in case_records]
    action_costs: List[float] = [r["action_cost"] for r in case_records]
    net_values: List[float] = [r["net_value"] for r in case_records]
    actions: List[str] = [r["action"] for r in case_records]

    recovered_count = int(sum(recovered_flags))
    total_recovered_amount = float(sum(recovered_amounts))
    total_action_cost = float(sum(action_costs))
    total_net_value = float(sum(net_values))

    # Recovery rate: fraction of cases where the account was recovered
    recovery_rate = safe_divide(recovered_count, n)

    # Average net value per case
    avg_net_value = safe_divide(total_net_value, n)

    # Stop rate: fraction of cases where the policy chose to take no action
    stop_count = int(sum(1 for a in actions if a == "stop"))
    stop_rate = safe_divide(stop_count, n)

    # Action distribution (raw counts)
    action_distribution: Dict[str, int] = dict(Counter(actions))

    # Average predicted ERV (only when the field is present)
    pred_ervs = [
        r["predicted_erv"]
        for r in case_records
        if r.get("predicted_erv") is not None
    ]
    avg_predicted_erv: Optional[float] = (
        safe_divide(sum(pred_ervs), len(pred_ervs)) if pred_ervs else None
    )

    # Guardrails triggered count (only when field is present)
    guardrails_triggered_total: Optional[int] = None
    guardrail_records = [
        r for r in case_records if r.get("guardrails_triggered") is not None
    ]
    if guardrail_records:
        guardrails_triggered_total = int(
            sum(r["guardrails_triggered"] for r in guardrail_records)
        )

    # Per-action recovery breakdown
    action_recovery: Dict[str, Dict[str, Any]] = {}
    for action in sorted(set(actions)):
        action_cases = [r for r in case_records if r["action"] == action]
        a_n = len(action_cases)
        a_recovered = int(sum(r["recovered"] for r in action_cases))
        a_net = float(sum(r["net_value"] for r in action_cases))
        action_recovery[action] = {
            "count": a_n,
            "recovered": a_recovered,
            "recovery_rate": round(safe_divide(a_recovered, a_n), 6),
            "total_net_value": round(a_net, 4),
        }

    return {
        "policy_name": policy_name,
        "n_cases": n,
        # Recovery outcomes
        "recovered_count": recovered_count,
        "recovery_rate": round(recovery_rate, 6),
        "total_recovered_amount": round(total_recovered_amount, 4),
        # Costs
        "total_action_cost": round(total_action_cost, 4),
        # Net value
        "total_net_value": round(total_net_value, 4),
        "avg_net_value_per_case": round(avg_net_value, 6),
        # Stop/no-action rate
        "stop_count": stop_count,
        "stop_rate": round(stop_rate, 6),
        # Action distribution
        "action_distribution": action_distribution,
        # Per-action breakdown
        "action_recovery_breakdown": action_recovery,
        # ML-only fields (None for baseline)
        "avg_predicted_erv": (
            round(avg_predicted_erv, 6) if avg_predicted_erv is not None else None
        ),
        "guardrails_triggered_total": guardrails_triggered_total,
    }


def _empty_metrics(policy_name: str) -> Dict[str, Any]:
    """Return a zeroed metrics dict when no case records are provided."""
    return {
        "policy_name": policy_name,
        "n_cases": 0,
        "recovered_count": 0,
        "recovery_rate": 0.0,
        "total_recovered_amount": 0.0,
        "total_action_cost": 0.0,
        "total_net_value": 0.0,
        "avg_net_value_per_case": 0.0,
        "stop_count": 0,
        "stop_rate": 0.0,
        "action_distribution": {},
        "action_recovery_breakdown": {},
        "avg_predicted_erv": None,
        "guardrails_triggered_total": None,
    }
