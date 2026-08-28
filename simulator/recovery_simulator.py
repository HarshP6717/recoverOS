"""
RecoverOS Ground-Truth Recovery Simulator.

Implements the deterministic/probabilistic ground-truth recovery dynamics for
failed subscription payment recovery actions in INR (₹).

NOTE: Action costs and probability dynamics are synthetic simulation assumptions
designed for controlled evaluation and experimentation, not Razorpay fees.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Union
import numpy as np
import pandas as pd

# Allowed recovery actions
ACTIONS: List[str] = [
    "retry_now",
    "retry_later",
    "send_reminder",
    "payment_method_update",
    "recovery_link",
    "escalate_human",
    "stop",
]

# Synthetic simulation assumptions for action execution costs in INR (₹)
# NOTE: These are synthetic assumptions, not actual gateway fees.
ACTION_COSTS: Dict[str, float] = {
    "retry_now": 1.00,
    "retry_later": 1.00,
    "send_reminder": 0.50,
    "payment_method_update": 2.00,
    "recovery_link": 1.50,
    "escalate_human": 30.00,
    "stop": 0.00,
}

# Permitted failure types
FAILURE_TYPES: List[str] = [
    "insufficient_funds",
    "bank_timeout",
    "soft_decline",
    "expired_card",
    "hard_decline",
    "invalid_payment_method",
    "customer_abandoned",
    "repeated_failure",
    "unknown",
]

# Supported payment methods
PAYMENT_METHODS: List[str] = [
    "upi",
    "card",
    "netbanking",
    "mandate_nach",
    "wallet",
]

# Base recovery probabilities by failure type and action
# Represents intrinsic effectiveness before contextual modifier adjustments
BASE_ACTION_PROBABILITIES: Dict[str, Dict[str, float]] = {
    "insufficient_funds": {
        "retry_now": 0.08,
        "retry_later": 0.68,
        "send_reminder": 0.42,
        "payment_method_update": 0.38,
        "recovery_link": 0.45,
        "escalate_human": 0.55,
        "stop": 0.00,
    },
    "bank_timeout": {
        "retry_now": 0.74,
        "retry_later": 0.79,
        "send_reminder": 0.18,
        "payment_method_update": 0.12,
        "recovery_link": 0.22,
        "escalate_human": 0.50,
        "stop": 0.00,
    },
    "soft_decline": {
        "retry_now": 0.38,
        "retry_later": 0.64,
        "send_reminder": 0.36,
        "payment_method_update": 0.46,
        "recovery_link": 0.42,
        "escalate_human": 0.58,
        "stop": 0.00,
    },
    "expired_card": {
        "retry_now": 0.01,
        "retry_later": 0.02,
        "send_reminder": 0.32,
        "payment_method_update": 0.82,
        "recovery_link": 0.74,
        "escalate_human": 0.72,
        "stop": 0.00,
    },
    "hard_decline": {
        "retry_now": 0.005,
        "retry_later": 0.01,
        "send_reminder": 0.12,
        "payment_method_update": 0.70,
        "recovery_link": 0.58,
        "escalate_human": 0.66,
        "stop": 0.00,
    },
    "invalid_payment_method": {
        "retry_now": 0.01,
        "retry_later": 0.015,
        "send_reminder": 0.28,
        "payment_method_update": 0.78,
        "recovery_link": 0.72,
        "escalate_human": 0.68,
        "stop": 0.00,
    },
    "customer_abandoned": {
        "retry_now": 0.05,
        "retry_later": 0.12,
        "send_reminder": 0.52,
        "payment_method_update": 0.36,
        "recovery_link": 0.68,
        "escalate_human": 0.70,
        "stop": 0.00,
    },
    "repeated_failure": {
        "retry_now": 0.04,
        "retry_later": 0.16,
        "send_reminder": 0.22,
        "payment_method_update": 0.62,
        "recovery_link": 0.54,
        "escalate_human": 0.76,
        "stop": 0.00,
    },
    "unknown": {
        "retry_now": 0.25,
        "retry_later": 0.40,
        "send_reminder": 0.28,
        "payment_method_update": 0.32,
        "recovery_link": 0.35,
        "escalate_human": 0.52,
        "stop": 0.00,
    },
}

# Method-specific affinity bonuses (in logit space)
METHOD_ACTION_LOGIT_BIAS: Dict[str, Dict[str, float]] = {
    "upi": {
        "retry_now": 0.10,
        "retry_later": 0.05,
        "send_reminder": 0.25,
        "recovery_link": 0.30,
        "payment_method_update": 0.10,
        "escalate_human": -0.10,
        "stop": 0.00,
    },
    "card": {
        "retry_now": 0.05,
        "retry_later": 0.05,
        "send_reminder": 0.00,
        "recovery_link": 0.10,
        "payment_method_update": 0.25,
        "escalate_human": 0.00,
        "stop": 0.00,
    },
    "netbanking": {
        "retry_now": 0.15,
        "retry_later": 0.10,
        "send_reminder": 0.10,
        "recovery_link": 0.15,
        "payment_method_update": 0.05,
        "escalate_human": 0.05,
        "stop": 0.00,
    },
    "mandate_nach": {
        "retry_now": -0.20,
        "retry_later": 0.35,
        "send_reminder": 0.10,
        "recovery_link": 0.05,
        "payment_method_update": 0.20,
        "escalate_human": 0.10,
        "stop": 0.00,
    },
    "wallet": {
        "retry_now": 0.05,
        "retry_later": 0.05,
        "send_reminder": 0.20,
        "recovery_link": 0.25,
        "payment_method_update": 0.15,
        "escalate_human": -0.15,
        "stop": 0.00,
    },
}


def _prob_to_logit(p: float) -> float:
    """Convert probability to log-odds (clamped to prevent overflow)."""
    p_safe = min(max(p, 1e-4), 1.0 - 1e-4)
    return math.log(p_safe / (1.0 - p_safe))


def _logit_to_prob(logit: float) -> float:
    """Convert log-odds back to probability with logistic sigmoid."""
    return 1.0 / (1.0 + math.exp(-logit))


def compute_ground_truth_recovery_probability(
    record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]],
    action: str,
) -> float:
    """
    Computes the action-specific ground-truth recovery probability for a payment record.

    Parameters
    ----------
    record : Union[Dict[str, Any], pd.Series, Mapping[str, Any]]
        Payment failure record containing features.
    action : str
        The recovery action to evaluate.

    Returns
    -------
    float
        Action-specific recovery probability in [0.0, 0.95]. Always 0.0 for 'stop'.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown recovery action: '{action}'. Must be one of {ACTIONS}")

    if action == "stop":
        return 0.0

    failure_type = str(record.get("failure_type", "unknown"))
    if failure_type not in BASE_ACTION_PROBABILITIES:
        failure_type = "unknown"

    payment_method = str(record.get("payment_method", "card"))
    if payment_method not in METHOD_ACTION_LOGIT_BIAS:
        payment_method = "card"

    # Base probability lookup
    base_p = BASE_ACTION_PROBABILITIES[failure_type][action]
    base_logit = _prob_to_logit(base_p)

    # 1. Payment method affinity bias
    method_bias = METHOD_ACTION_LOGIT_BIAS[payment_method].get(action, 0.0)

    # 2. Previous payment / success history
    prev_payments = float(record.get("previous_payment_count", 0))
    prev_success = float(record.get("previous_success_count", 0))
    prev_failures = float(record.get("previous_failure_count", 0))
    prev_recoveries = float(record.get("previous_recovery_count", 0))

    if prev_payments > 0:
        success_rate = prev_success / prev_payments
    else:
        success_rate = 0.5  # Neutral default for new customers

    if prev_failures > 0:
        recovery_rate = prev_recoveries / prev_failures
    else:
        recovery_rate = 0.5  # Neutral default

    # Good customer history boosts willingness/responsiveness
    history_bias = 0.60 * (success_rate - 0.50) + 0.35 * (recovery_rate - 0.50)

    # 3. Attempt number penalty (diminishing returns on repeated attempts)
    attempt_num = max(1, int(record.get("attempt_number", 1)))
    attempt_penalty = -0.32 * (attempt_num - 1)

    # 4. Days overdue penalty (older debt decays in recovery likelihood)
    days_overdue = max(0, float(record.get("days_overdue", 0)))
    overdue_penalty = -0.035 * min(days_overdue, 45.0)

    # 5. Contact fatigue penalty (repeated contacts cause fatigue)
    contact_count = max(0, int(record.get("contact_count", 0)))
    fatigue_penalty = -0.12 * max(0, contact_count - 2)

    # 6. Customer Lifetime Value / loyalty loyalty bonus (in INR ₹)
    clv = max(0.0, float(record.get("customer_lifetime_value", 0.0)))
    clv_loyalty_boost = 0.20 * min(1.0, clv / 15000.0)

    # Combine logit terms
    total_logit = (
        base_logit
        + method_bias
        + history_bias
        + attempt_penalty
        + overdue_penalty
        + fatigue_penalty
        + clv_loyalty_boost
    )

    prob = _logit_to_prob(total_logit)

    # Hard ceiling for realism (no action is 100% guaranteed in payment recovery)
    return float(np.clip(prob, 0.001, 0.95))


def compute_expected_recovery_value(
    amount: float,
    recovery_probability: float,
    action: str,
) -> float:
    """
    Calculates Expected Recovery Value (ERV) in INR (₹):
    ERV = amount * recovery_probability - action_cost

    Parameters
    ----------
    amount : float
        Invoice amount at risk in INR (₹).
    recovery_probability : float
        Probability of recovery for this action.
    action : str
        Recovery action.

    Returns
    -------
    float
        Expected net recovery value in INR (₹).
    """
    cost = ACTION_COSTS.get(action, 0.0)
    return float((amount * recovery_probability) - cost)


def evaluate_all_actions_ground_truth(
    record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """
    Calculates ground-truth recovery probabilities, action costs, and ERVs
    for all 7 allowed actions on a given record.

    Returns
    -------
    Dict[str, Dict[str, float]]
        Mapping of action name -> {'recovery_probability', 'action_cost', 'expected_recovery_value'}
    """
    amount = float(record.get("amount", 0.0))
    results = {}
    for action in ACTIONS:
        prob = compute_ground_truth_recovery_probability(record, action)
        cost = ACTION_COSTS[action]
        erv = compute_expected_recovery_value(amount, prob, action)
        results[action] = {
            "recovery_probability": prob,
            "action_cost": cost,
            "expected_recovery_value": erv,
        }
    return results


def simulate_action(
    record: Union[Dict[str, Any], pd.Series, Mapping[str, Any]],
    action: str,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Simulates the execution of a recovery action using the ground-truth recovery simulator.

    Separates decision logic from outcome generation. Outcome is determined via a
    Bernoulli trial using the ground-truth recovery probability.

    Parameters
    ----------
    record : Union[Dict[str, Any], pd.Series, Mapping[str, Any]]
        Payment failure record.
    action : str
        Selected recovery action.
    seed : Optional[int]
        Random seed for Bernoulli trial reproducibility.

    Returns
    -------
    Dict[str, Any]
        Dictionary containing:
        - action: str
        - recovery_probability: float (ground-truth)
        - action_cost: float (in ₹)
        - expected_recovery_value: float (in ₹)
        - recovered: bool (simulated Bernoulli outcome)
        - recovered_amount: float (amount if recovered else 0.0, in ₹)
        - net_value: float (recovered_amount - action_cost, in ₹)
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: '{action}'. Must be one of {ACTIONS}")

    amount = float(record.get("amount", 0.0))
    prob = compute_ground_truth_recovery_probability(record, action)
    cost = ACTION_COSTS[action]
    erv = compute_expected_recovery_value(amount, prob, action)

    if action == "stop":
        recovered = False
    else:
        rng = np.random.default_rng(seed)
        recovered = bool(rng.random() < prob)

    recovered_amount = amount if recovered else 0.0
    net_value = recovered_amount - cost

    return {
        "action": action,
        "recovery_probability": prob,
        "action_cost": cost,
        "expected_recovery_value": erv,
        "recovered": recovered,
        "recovered_amount": recovered_amount,
        "net_value": net_value,
    }
