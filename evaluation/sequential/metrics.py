"""
RecoverOS Phase 2C Step 4 — Sequential Metrics Computation.

Aggregates sequential case states into comprehensive journey metrics:
- Overall financial & recovery outcomes
- Cumulative round-by-round progression (R1, R2, R3)
- Efficiency metrics (actions/case, cost/recovered case)
- Termination reasons & action distributions by round
- Multi-step transition pathways
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List
from evaluation.sequential.state import SequentialCaseState
from simulator.recovery_simulator import ACTIONS


def safe_divide(n: float, d: float, default: float = 0.0) -> float:
    return default if d == 0.0 else n / d


def compute_sequential_metrics(
    states: List[SequentialCaseState],
    policy_name: str,
) -> Dict[str, Any]:
    """
    Computes summary metrics for a sequential evaluation run.
    """
    n = len(states)
    if n == 0:
        return {"policy_name": policy_name, "n_cases": 0}

    recovered_flags = [s.is_recovered for s in states]
    recovered_count = sum(recovered_flags)
    recovery_rate = safe_divide(recovered_count, n)

    tot_rec_amount = sum(s.cumulative_recovered_amount for s in states)
    tot_cost = sum(s.cumulative_action_cost for s in states)
    tot_net_val = sum(s.cumulative_net_value for s in states)
    avg_net_val = safe_divide(tot_net_val, n)

    # Cumulative recovery by round
    rec_r1 = sum(1 for s in states if s.recovered_round == 1)
    rec_r2 = sum(1 for s in states if s.recovered_round == 2)
    rec_r3 = sum(1 for s in states if s.recovered_round == 3)

    cum_rec_r1 = rec_r1
    cum_rec_r2 = rec_r1 + rec_r2
    cum_rec_r3 = rec_r1 + rec_r2 + rec_r3

    cum_rr_r1 = safe_divide(cum_rec_r1, n)
    cum_rr_r2 = safe_divide(cum_rec_r2, n)
    cum_rr_r3 = safe_divide(cum_rec_r3, n)

    # Cumulative financial value by round
    # Reconstruct round-by-round net value
    r1_net_val = 0.0
    r2_net_val = 0.0
    r3_net_val = 0.0

    for s in states:
        for ro in s.round_outcomes:
            r = ro["round"]
            amt = ro["recovered_amount"]
            cost = ro["action_cost"]
            nv = amt - cost
            if r == 1:
                r1_net_val += nv
            elif r == 2:
                r2_net_val += nv
            elif r == 3:
                r3_net_val += nv

    cum_nv_r1 = r1_net_val
    cum_nv_r2 = r1_net_val + r2_net_val
    cum_nv_r3 = r1_net_val + r2_net_val + r3_net_val

    # Action efficiency
    total_actions = sum(len(s.action_history) for s in states)
    avg_actions_per_case = safe_divide(total_actions, n)

    actions_recovered_cases = sum(len(s.action_history) for s in states if s.is_recovered)
    avg_actions_per_recovered_case = safe_divide(actions_recovered_cases, recovered_count)

    cost_per_recovered_case = safe_divide(tot_cost, recovered_count)
    unrecovered_count = n - recovered_count

    # Termination reason breakdown
    term_counts = Counter(s.termination_reason for s in states)
    termination_breakdown = {
        reason: {
            "count": count,
            "pct": round(safe_divide(count, n) * 100, 2),
        }
        for reason, count in sorted(term_counts.items())
    }

    stop_count = term_counts.get("STOP_ACTION", 0)
    stop_rate = safe_divide(stop_count, n)
    escalate_count = term_counts.get("ESCALATE_ACTION", 0)
    escalate_rate = safe_divide(escalate_count, n)

    # Action distribution by round
    action_dist_by_round: Dict[int, Dict[str, int]] = {1: defaultdict(int), 2: defaultdict(int), 3: defaultdict(int)}
    for s in states:
        for r_idx, act in enumerate(s.action_history, start=1):
            if r_idx <= 3:
                action_dist_by_round[r_idx][act] += 1

    action_distribution_by_round = {
        f"round_{r}": {act: action_dist_by_round[r].get(act, 0) for act in ACTIONS if action_dist_by_round[r].get(act, 0) > 0}
        for r in [1, 2, 3]
    }

    # Transition pathways (e.g. 'retry_later -> payment_method_update')
    pathway_counts = Counter(" -> ".join(s.action_history) for s in states)
    top_pathways = {
        path: {
            "count": count,
            "pct": round(safe_divide(count, n) * 100, 2),
        }
        for path, count in pathway_counts.most_common(15)
    }

    return {
        "policy_name": policy_name,
        "n_cases": n,
        "recovered_count": recovered_count,
        "recovery_rate": round(recovery_rate, 6),
        "total_recovered_amount": round(tot_rec_amount, 4),
        "total_action_cost": round(tot_cost, 4),
        "total_net_value": round(tot_net_val, 4),
        "avg_net_value_per_case": round(avg_net_val, 4),
        # Round progression
        "round_progression": {
            "round_1": {
                "recovered_in_round": rec_r1,
                "cumulative_recovered": cum_rec_r1,
                "cumulative_recovery_rate": round(cum_rr_r1, 6),
                "round_net_value": round(r1_net_val, 4),
                "cumulative_net_value": round(cum_nv_r1, 4),
            },
            "round_2": {
                "recovered_in_round": rec_r2,
                "cumulative_recovered": cum_rec_r2,
                "cumulative_recovery_rate": round(cum_rr_r2, 6),
                "round_net_value": round(r2_net_val, 4),
                "cumulative_net_value": round(cum_nv_r2, 4),
            },
            "round_3": {
                "recovered_in_round": rec_r3,
                "cumulative_recovered": cum_rec_r3,
                "cumulative_recovery_rate": round(cum_rr_r3, 6),
                "round_net_value": round(r3_net_val, 4),
                "cumulative_net_value": round(cum_nv_r3, 4),
            },
        },
        # Action efficiency
        "total_actions_executed": total_actions,
        "avg_actions_per_case": round(avg_actions_per_case, 4),
        "avg_actions_per_recovered_case": round(avg_actions_per_recovered_case, 4),
        "cost_per_recovered_case": round(cost_per_recovered_case, 4),
        "unrecovered_cases": unrecovered_count,
        "stop_rate": round(stop_rate, 6),
        "escalation_rate": round(escalate_rate, 6),
        "termination_breakdown": termination_breakdown,
        "action_distribution_by_round": action_distribution_by_round,
        "top_pathways": top_pathways,
    }
