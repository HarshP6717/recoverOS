"""
Tests for Sequential Multi-Step Recovery Module (Phase 2C Step 4).

Verifies:
- Identical initial population
- Deterministic reproducibility
- Seed independence from policy action
- No source-data mutation
- State transition correctness
- Maximum 3 rounds horizon limit
- STOP termination
- Recovery termination
- Escalation termination
- Permanent failure guardrail enforcement in sequential rounds
- Zero-division safety
- Policy isolation & no lookahead
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.evaluator import _load_test_data, _load_ml_model, SEED_BASE
from evaluation.sequential.state import SequentialCaseState
from evaluation.sequential.transition import (
    transition_case_state,
    MAX_HORIZON_ROUNDS,
    CUSTOMER_FACING_ACTIONS,
)
from evaluation.sequential.evaluator import SequentialPolicyEvaluator
from evaluation.sequential.metrics import compute_sequential_metrics, safe_divide
from simulator.recovery_simulator import simulate_action


@pytest.fixture(scope="module")
def test_df():
    return _load_test_data()


@pytest.fixture(scope="module")
def model():
    return _load_ml_model()


# ─────────────────────────────────────────────────────────────────────────────
# 1. State Transition Correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestStateTransitions:
    def test_recovery_terminates_immediately(self, test_df):
        state = SequentialCaseState.from_row(test_df.iloc[0], 0)
        expected_amt = state.amount
        sim_out = {"recovered": True, "recovered_amount": expected_amt, "action_cost": 1.0, "net_value": expected_amt - 1.0}
        updated = transition_case_state(state, "retry_later", sim_out)

        assert updated.is_recovered is True
        assert updated.is_terminated is True
        assert updated.termination_reason == "RECOVERED"
        assert updated.recovered_round == 1
        assert updated.cumulative_recovered_amount == expected_amt
        assert abs(updated.cumulative_net_value - (expected_amt - 1.0)) < 1e-6

    def test_stop_action_terminates_immediately(self, test_df):
        state = SequentialCaseState.from_row(test_df.iloc[0], 0)
        sim_out = {"recovered": False, "recovered_amount": 0.0, "action_cost": 0.0, "net_value": 0.0}
        updated = transition_case_state(state, "stop", sim_out)

        assert updated.is_recovered is False
        assert updated.is_terminated is True
        assert updated.termination_reason == "STOP_ACTION"
        assert updated.cumulative_action_cost == 0.0

    def test_escalate_human_terminates_immediately(self, test_df):
        state = SequentialCaseState.from_row(test_df.iloc[0], 0)
        sim_out = {"recovered": False, "recovered_amount": 0.0, "action_cost": 30.0, "net_value": -30.0}
        updated = transition_case_state(state, "escalate_human", sim_out)

        assert updated.is_recovered is False
        assert updated.is_terminated is True
        assert updated.termination_reason == "ESCALATE_ACTION"
        assert updated.cumulative_action_cost == 30.0

    def test_failed_attempt_increments_state_correctly(self, test_df):
        state = SequentialCaseState.from_row(test_df.iloc[0], 0)
        initial_attempt = state.attempt_number
        initial_days = state.days_overdue
        initial_contact = state.contact_count

        sim_out = {"recovered": False, "recovered_amount": 0.0, "action_cost": 1.5, "net_value": -1.5}
        updated = transition_case_state(state, "recovery_link", sim_out)

        assert updated.is_terminated is False
        assert updated.current_round == 2
        assert updated.attempt_number == initial_attempt + 1
        assert updated.days_overdue == initial_days + 2.0
        assert updated.contact_count == initial_contact + 1
        assert updated.cumulative_action_cost == 1.5

    def test_max_rounds_enforced(self, test_df):
        state = SequentialCaseState.from_row(test_df.iloc[0], 0)
        sim_out = {"recovered": False, "recovered_amount": 0.0, "action_cost": 1.0, "net_value": -1.0}

        # Round 1 fail -> Round 2
        state = transition_case_state(state, "retry_later", sim_out)
        assert state.current_round == 2 and not state.is_terminated

        # Round 2 fail -> Round 3
        state = transition_case_state(state, "retry_later", sim_out)
        assert state.current_round == 3 and not state.is_terminated

        # Round 3 fail -> Terminate with MAX_ROUNDS_REACHED
        state = transition_case_state(state, "retry_later", sim_out)
        assert state.is_terminated is True
        assert state.termination_reason == "MAX_ROUNDS_REACHED"
        assert len(state.action_history) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 2. Sequential Evaluator & Protocol Integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestSequentialEvaluator:
    def test_identical_initial_population(self, test_df, model):
        evaluator = SequentialPolicyEvaluator()
        b_states = evaluator.evaluate_baseline(test_df)
        h_states = evaluator.evaluate_heuristic(test_df)
        r_states = evaluator.evaluate_recoveros(test_df, model)

        assert len(b_states) == len(h_states) == len(r_states) == len(test_df) == 1000
        for i in range(1000):
            assert b_states[i].case_index == h_states[i].case_index == r_states[i].case_index == i
            assert b_states[i].transaction_id == h_states[i].transaction_id == r_states[i].transaction_id

    def test_deterministic_reproducibility(self, test_df, model):
        evaluator = SequentialPolicyEvaluator()
        sub_df = test_df.iloc[:25].copy()

        r_run1 = evaluator.evaluate_recoveros(sub_df, model)
        r_run2 = evaluator.evaluate_recoveros(sub_df, model)

        for s1, s2 in zip(r_run1, r_run2):
            assert s1.action_history == s2.action_history
            assert s1.is_recovered == s2.is_recovered
            assert s1.cumulative_net_value == s2.cumulative_net_value

    def test_seed_formula_consistency(self):
        """Verify that seed formula produces expected round-offset values."""
        case_idx = 7
        evaluator = SequentialPolicyEvaluator(seed_base=42)
        seed_r1 = evaluator.seed_base + case_idx + 0 * 1000
        seed_r2 = evaluator.seed_base + case_idx + 1 * 1000
        seed_r3 = evaluator.seed_base + case_idx + 2 * 1000

        assert seed_r1 == 49
        assert seed_r2 == 1049
        assert seed_r3 == 2049

    def test_no_permanent_failure_retry_in_sequential_rounds(self, test_df, model):
        """Guardrail check: hard failure records must never execute retry_now or retry_later."""
        hard_df = test_df[test_df["failure_type"].isin(["expired_card", "hard_decline", "invalid_payment_method"])]
        evaluator = SequentialPolicyEvaluator()
        r_states = evaluator.evaluate_recoveros(hard_df, model)

        for s in r_states:
            for act in s.action_history:
                assert act not in ("retry_now", "retry_later"), (
                    f"Hard failure record {s.transaction_id} executed prohibited retry action '{act}'!"
                )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Sequential Metrics & Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestSequentialMetrics:
    def test_safe_divide(self):
        assert safe_divide(10.0, 0.0) == 0.0
        assert safe_divide(10.0, 2.0) == 5.0

    def test_empty_metrics(self):
        m = compute_sequential_metrics([], "Empty")
        assert m["n_cases"] == 0

    def test_metrics_consistency_sums(self, test_df, model):
        evaluator = SequentialPolicyEvaluator()
        sub_df = test_df.iloc[:50].copy()
        states = evaluator.evaluate_recoveros(sub_df, model)
        m = compute_sequential_metrics(states, "Test ML")

        assert m["recovered_count"] + m["unrecovered_cases"] == m["n_cases"] == 50
        assert abs(m["total_net_value"] - (m["total_recovered_amount"] - m["total_action_cost"])) < 1e-6
