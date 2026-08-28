"""
Tests for RecoverOS Recovery Policies and Deterministic Guardrails.
"""

import pytest
import numpy as np
import pandas as pd

from simulator.policies import (
    DeterministicBaselinePolicy,
    GroundTruthOraclePolicy,
    MLExpectedValuePolicy,
)
from simulator.recovery_simulator import evaluate_all_actions_ground_truth
from ml.train import load_model_artifact


@pytest.fixture
def baseline_policy() -> DeterministicBaselinePolicy:
    return DeterministicBaselinePolicy()


@pytest.fixture
def oracle_policy() -> GroundTruthOraclePolicy:
    return GroundTruthOraclePolicy()


@pytest.fixture
def trained_ml_policy() -> MLExpectedValuePolicy:
    model = load_model_artifact()
    return MLExpectedValuePolicy(model, guardrails_enabled=True)


def test_deterministic_baseline_rules(baseline_policy):
    """Verify deterministic rule assignments across attempts and failure types."""
    # Attempt 1 transient
    rec1 = {"attempt_number": 1, "failure_type": "insufficient_funds"}
    assert baseline_policy.select_action(rec1) == "retry_now"

    # Attempt 1 hard failure
    rec2 = {"attempt_number": 1, "failure_type": "expired_card"}
    assert baseline_policy.select_action(rec2) == "payment_method_update"

    # Attempt 2 transient
    rec3 = {"attempt_number": 2, "failure_type": "bank_timeout"}
    assert baseline_policy.select_action(rec3) == "retry_later"

    # Attempt 2 hard failure
    rec4 = {"attempt_number": 2, "failure_type": "hard_decline"}
    assert baseline_policy.select_action(rec4) == "recovery_link"

    # Attempt 3
    rec5 = {"attempt_number": 3, "failure_type": "insufficient_funds"}
    assert baseline_policy.select_action(rec5) == "send_reminder"

    # Attempt 4+
    rec6 = {"attempt_number": 4, "failure_type": "bank_timeout"}
    assert baseline_policy.select_action(rec6) == "stop"


def test_oracle_policy_selects_max_erv(oracle_policy):
    """Verify GroundTruthOracle selects action with highest ground-truth ERV."""
    record = {
        "amount": 2500.0,
        "payment_method": "card",
        "failure_type": "expired_card",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 6,
        "previous_success_count": 6,
        "previous_failure_count": 0,
        "previous_recovery_count": 0,
        "customer_lifetime_value": 15000.0,
        "contact_count": 0,
        "subscription_age_days": 180,
    }
    all_gt = evaluate_all_actions_ground_truth(record)
    expected_best = max(all_gt.keys(), key=lambda a: all_gt[a]["expected_recovery_value"])

    selected = oracle_policy.select_action(record)
    assert selected == expected_best


def test_ml_policy_guardrail_hard_failure_retry_suppression(trained_ml_policy):
    """Verify ML policy guardrail suppresses automated retries on permanent failures."""
    record = {
        "amount": 1499.0,
        "payment_method": "card",
        "failure_type": "expired_card",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 4,
        "previous_success_count": 4,
        "previous_failure_count": 0,
        "previous_recovery_count": 0,
        "customer_lifetime_value": 5996.0,
        "contact_count": 0,
        "subscription_age_days": 120,
    }
    action = trained_ml_policy.select_action(record)
    assert action not in ["retry_now", "retry_later"], f"Retries must be suppressed on expired card, got {action}"


def test_ml_policy_guardrail_micro_amount_human_escalation(trained_ml_policy):
    """Verify ML policy guardrail suppresses ₹30 human escalation on micro-invoices (< ₹100)."""
    record = {
        "amount": 49.0,  # Micro amount in INR
        "payment_method": "upi",
        "failure_type": "customer_abandoned",
        "attempt_number": 3,
        "days_overdue": 10,
        "previous_payment_count": 1,
        "previous_success_count": 1,
        "previous_failure_count": 0,
        "previous_recovery_count": 0,
        "customer_lifetime_value": 49.0,
        "contact_count": 2,
        "subscription_age_days": 30,
    }
    action = trained_ml_policy.select_action(record)
    assert action != "escalate_human", "Human escalation (₹30) must be suppressed for invoice amount < ₹100"
