"""
RecoverOS Evaluation Pipeline.

Performs:
1. Statistical ML Model Evaluation on Held-Out Test Set:
   - ROC-AUC, PR-AUC, Log Loss, Brier Score, Expected Calibration Error (ECE).
2. Counterfactual Policy Simulation on Held-Out Test Set (1,000 records):
   - Evaluates:
     a) Deterministic Baseline Policy (fixed dunning heuristics)
     b) RecoverOS ML Policy (Predicted ERV in INR ₹ + Deterministic Guardrails)
     c) Ground-Truth Oracle Policy — theoretical upper bound
   - For every candidate action, the policy decides the action, and the INDEPENDENT
     ground-truth simulator simulates the actual outcome using ground-truth probabilities
     and Bernoulli trial.
   - Evaluates financial ROI (in INR ₹), recovery rate, costs, and action distributions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Enable UTF-8 for console output on Windows platforms
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.train import load_model_artifact
from ml.transformers import FeatureEngineeringTransformer
from simulator.policies import (
    BasePolicy,
    DeterministicBaselinePolicy,
    GroundTruthOraclePolicy,
    MLExpectedValuePolicy,
)
from simulator.recovery_simulator import (
    ACTIONS,
    ACTION_COSTS,
    simulate_action,
)

DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def compute_expected_calibration_error(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Computes the Expected Calibration Error (ECE) across n_bins.

    ECE = sum_{b=1}^B (|acc(b) - conf(b)| * (|b| / N))
    """
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_indices = np.digitize(y_prob, bin_edges, right=True) - 1
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)

    ece = 0.0
    n_samples = len(y_true)

    for b in range(n_bins):
        mask = bin_indices == b
        bin_count = np.sum(mask)
        if bin_count > 0:
            bin_acc = np.mean(y_true[mask])
            bin_conf = np.mean(y_prob[mask])
            ece += (bin_count / n_samples) * abs(bin_acc - bin_conf)

    return float(ece)


def evaluate_ml_model(
    model: Any,
    test_df: pd.DataFrame,
) -> Dict[str, float]:
    """
    Evaluates ML classification and probability calibration metrics on the test set.

    Parameters
    ----------
    model : Any
        Trained model pipeline.
    test_df : pd.DataFrame
        Held-out test set records.

    Returns
    -------
    Dict[str, float]
        Dictionary of ML metrics.
    """
    y_true = test_df["recovered"].astype(int).values
    y_prob = model.predict_proba(test_df)[:, 1]

    roc_auc = float(roc_auc_score(y_true, y_prob))
    pr_auc = float(average_precision_score(y_true, y_prob))
    ll = float(log_loss(y_true, y_prob))
    brier = float(brier_score_loss(y_true, y_prob))
    ece = compute_expected_calibration_error(y_true, y_prob, n_bins=10)

    return {
        "roc_auc": round(roc_auc, 4),
        "pr_auc": round(pr_auc, 4),
        "log_loss": round(ll, 4),
        "brier_score": round(brier, 4),
        "expected_calibration_error": round(ece, 4),
    }


def simulate_policy_on_test_set(
    policy: BasePolicy,
    test_df: pd.DataFrame,
    seed_base: int = 10000,
) -> Dict[str, Any]:
    """
    Simulates a recovery policy on the held-out test set.

    For each test record:
    1. The policy selects an action.
    2. The independent ground-truth simulator executes the action, generating
       actual simulated outcomes (recovered, cost, recovered_amount, net_value).

    Parameters
    ----------
    policy : BasePolicy
        Policy instance to evaluate.
    test_df : pd.DataFrame
        Held-out test set records (1,000 cases).
    seed_base : int
        Base seed for consistent, fair Bernoulli trial sampling across policies.

    Returns
    -------
    Dict[str, Any]
        Dictionary of financial and operational recovery metrics.
    """
    n_cases = len(test_df)
    total_invoices_at_risk = float(test_df["amount"].sum())

    # Get policy decisions
    chosen_actions = policy.select_actions_batch(test_df)

    # Simulate outcomes via independent ground-truth simulator
    sim_recovered = []
    sim_costs = []
    sim_recovered_amounts = []
    sim_net_values = []
    action_counts: Dict[str, int] = {a: 0 for a in ACTIONS}

    for i in range(n_cases):
        row = test_df.iloc[i]
        act = chosen_actions[i]
        action_counts[act] = action_counts.get(act, 0) + 1

        # Bernoulli trial using ground-truth probability
        sim_res = simulate_action(row, act, seed=seed_base + i)

        sim_recovered.append(sim_res["recovered"])
        sim_costs.append(sim_res["action_cost"])
        sim_recovered_amounts.append(sim_res["recovered_amount"])
        sim_net_values.append(sim_res["net_value"])

    total_cost = float(sum(sim_costs))
    total_recovered_revenue = float(sum(sim_recovered_amounts))
    total_net_value = float(sum(sim_net_values))
    recovered_count = int(sum(sim_recovered))

    gross_recovery_rate = (total_recovered_revenue / total_invoices_at_risk) * 100.0 if total_invoices_at_risk > 0 else 0.0
    net_recovery_rate = (total_net_value / total_invoices_at_risk) * 100.0 if total_invoices_at_risk > 0 else 0.0
    case_success_rate = (recovered_count / n_cases) * 100.0 if n_cases > 0 else 0.0

    return {
        "policy_name": policy.name,
        "n_cases": n_cases,
        "total_invoices_at_risk": round(total_invoices_at_risk, 2),
        "total_cost": round(total_cost, 2),
        "total_recovered_revenue": round(total_recovered_revenue, 2),
        "total_net_value": round(total_net_value, 2),
        "recovered_count": recovered_count,
        "case_success_rate_pct": round(case_success_rate, 2),
        "gross_recovery_rate_pct": round(gross_recovery_rate, 2),
        "net_recovery_rate_pct": round(net_recovery_rate, 2),
        "action_distribution": action_counts,
    }


def format_currency(val: float) -> str:
    """Formats float as INR currency string."""
    return f"₹{val:,.2f}"


def run_full_evaluation() -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """
    Executes full evaluation on held-out test set and prints formatted summary reports.
    """
    test_path = DATA_PROCESSED_DIR / "test.csv"
    if not test_path.exists():
        raise FileNotFoundError(f"Test dataset not found at {test_path}. Run synthetic generator first.")

    test_df = pd.read_csv(test_path)
    model = load_model_artifact()

    print("=" * 80)
    print("RECOVEROS PHASE 1 EVALUATION REPORT")
    print("=" * 80)
    print(f"Held-Out Test Dataset: {len(test_df)} records (Strictly zero data leakage)")
    print(f"Total Invoices at Risk: {format_currency(test_df['amount'].sum())}\n")

    # 1. Statistical ML Model Evaluation
    print("-" * 80)
    print("1. MACHINE LEARNING MODEL PERFORMANCE (Held-Out Test Set)")
    print("-" * 80)
    ml_metrics = evaluate_ml_model(model, test_df)
    for k, v in ml_metrics.items():
        print(f"  {k:<30}: {v}")

    # 2. Counterfactual Policy Simulations
    print("\n" + "-" * 80)
    print("2. COUNTERFACTUAL POLICY SIMULATION COMPARISON (1,000 Test Cases)")
    print("   (Outcomes generated independently by Ground-Truth Simulator in INR ₹)")
    print("-" * 80)

    policies: List[BasePolicy] = [
        DeterministicBaselinePolicy(),
        MLExpectedValuePolicy(model, guardrails_enabled=True),
        GroundTruthOraclePolicy(),
    ]

    policy_results: Dict[str, Dict[str, Any]] = {}

    for pol in policies:
        res = simulate_policy_on_test_set(pol, test_df, seed_base=10000)
        policy_results[pol.name] = res

    # Comparison summary table
    print(f"\n{'Policy':<48} | {'Recovered (₹)':<14} | {'Costs (₹)':<11} | {'Net Value (₹)':<14} | {'Net Rec %':<9} | {'Resolved %':<10}")
    print("-" * 118)
    for name, res in policy_results.items():
        rec_str = format_currency(res["total_recovered_revenue"])
        cost_str = format_currency(res["total_cost"])
        net_str = format_currency(res["total_net_value"])
        net_pct = f"{res['net_recovery_rate_pct']:.2f}%"
        res_pct = f"{res['case_success_rate_pct']:.2f}%"
        print(f"{name:<48} | {rec_str:<14} | {cost_str:<11} | {net_str:<14} | {net_pct:<9} | {res_pct:<10}")

    # Action distributions
    print("\n" + "-" * 80)
    print("3. ACTION DISTRIBUTION BREAKDOWN")
    print("-" * 80)
    act_header = f"{'Policy':<48} | " + " | ".join([f"{a[:10]:<10}" for a in ACTIONS])
    print(act_header)
    print("-" * len(act_header))
    for name, res in policy_results.items():
        dist = res["action_distribution"]
        dist_strs = [f"{dist.get(a, 0):<10}" for a in ACTIONS]
        print(f"{name:<48} | " + " | ".join(dist_strs))

    print("\n" + "=" * 80)
    print("NOTE: Ground-Truth Oracle uses unobservable true probabilities as a theoretical")
    print("upper bound and is NOT available in production. RecoverOS ML uses predicted ERV")
    print("with deterministic guardrails.")
    print("=" * 80)

    return ml_metrics, policy_results


if __name__ == "__main__":
    run_full_evaluation()
