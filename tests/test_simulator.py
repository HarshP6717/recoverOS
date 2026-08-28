"""
Tests for RecoverOS Ground-Truth Recovery Simulator and Synthetic Generator.
"""

import math
import pytest
import numpy as np
import pandas as pd

from simulator.recovery_simulator import (
    ACTIONS,
    ACTION_COSTS,
    FAILURE_TYPES,
    PAYMENT_METHODS,
    compute_expected_recovery_value,
    compute_ground_truth_recovery_probability,
    evaluate_all_actions_ground_truth,
    simulate_action,
)
from ml.synthetic_generator import generate_synthetic_dataset, save_dataset_splits


@pytest.fixture
def sample_record() -> dict:
    return {
        "transaction_id": "tx_00001",
        "customer_id": "cust_00001",
        "subscription_id": "sub_00001",
        "amount": 999.0,
        "payment_method": "upi",
        "failure_type": "insufficient_funds",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 12,
        "previous_success_count": 11,
        "previous_failure_count": 1,
        "previous_recovery_count": 1,
        "customer_lifetime_value": 11988.0,
        "contact_count": 0,
        "subscription_age_days": 365,
    }


def test_action_costs_in_inr():
    """Verify synthetic action costs match the specified INR values."""
    expected_costs = {
        "retry_now": 1.00,
        "retry_later": 1.00,
        "send_reminder": 0.50,
        "payment_method_update": 2.00,
        "recovery_link": 1.50,
        "escalate_human": 30.00,
        "stop": 0.00,
    }
    for action, expected_cost in expected_costs.items():
        assert ACTION_COSTS[action] == expected_cost, f"Cost for {action} should be ₹{expected_cost}"


def test_recovery_probability_bounds(sample_record):
    """Verify recovery probability is clamped and valid for all actions."""
    for action in ACTIONS:
        prob = compute_ground_truth_recovery_probability(sample_record, action)
        if action == "stop":
            assert prob == 0.0, "Stop action probability must be exactly 0.0"
        else:
            assert 0.001 <= prob <= 0.95, f"Probability {prob} for {action} out of bounds"


def test_expected_recovery_value_formula():
    """Verify ERV = amount * recovery_probability - action_cost in INR."""
    amount = 1000.0
    prob = 0.60
    action = "payment_method_update"  # cost = 2.00
    erv = compute_expected_recovery_value(amount, prob, action)
    expected = (1000.0 * 0.60) - 2.00  # 598.00
    assert math.isclose(erv, expected, rel_tol=1e-6)

    # Stop action
    stop_erv = compute_expected_recovery_value(amount, 0.0, "stop")
    assert stop_erv == 0.0


def test_evaluate_all_actions_ground_truth(sample_record):
    """Verify ground-truth evaluation for all 7 actions."""
    results = evaluate_all_actions_ground_truth(sample_record)
    assert len(results) == 7
    for action in ACTIONS:
        assert action in results
        assert "recovery_probability" in results[action]
        assert "action_cost" in results[action]
        assert "expected_recovery_value" in results[action]


def test_simulate_action_stop(sample_record):
    """Verify simulation of stop action returns 0 recovered and 0 cost."""
    res = simulate_action(sample_record, "stop", seed=42)
    assert res["action"] == "stop"
    assert res["recovered"] is False
    assert res["recovered_amount"] == 0.0
    assert res["action_cost"] == 0.0
    assert res["net_value"] == 0.0


def test_simulate_action_determinism(sample_record):
    """Verify identical random seeds yield identical simulation results."""
    res1 = simulate_action(sample_record, "retry_later", seed=12345)
    res2 = simulate_action(sample_record, "retry_later", seed=12345)
    assert res1 == res2


def test_synthetic_dataset_properties():
    """Verify 10,000 dataset generation constraints and feature integrity."""
    df = generate_synthetic_dataset(n_records=1000, seed=42)  # Test with 1,000 for fast test suite
    assert len(df) == 1000

    # Null check
    assert df.isnull().sum().sum() == 0, "Synthetic dataset contains null values"

    # Numeric integrity
    assert (df["amount"] > 0).all(), "Amounts must be strictly positive"
    assert (df["previous_success_count"] <= df["previous_payment_count"]).all()
    assert (df["previous_failure_count"] == df["previous_payment_count"] - df["previous_success_count"]).all()
    assert (df["previous_recovery_count"] <= df["previous_failure_count"]).all()
    assert (df["days_overdue"] >= 0).all()
    assert (df["contact_count"] >= 0).all()

    # Categorical domain checks
    assert set(df["failure_type"]).issubset(set(FAILURE_TYPES))
    assert set(df["payment_method"]).issubset(set(PAYMENT_METHODS))
    assert set(df["action"]).issubset(set(ACTIONS))
