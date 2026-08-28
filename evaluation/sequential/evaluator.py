"""
RecoverOS Phase 2C Step 4 — Sequential Policy Evaluator.

Executes sequential multi-step journeys (up to 3 rounds) for:
- DeterministicBaselinePolicy
- StrongFeatureAwareHeuristic
- MLExpectedValuePolicy (RecoverOS)

SEED PROTOCOL:
For case i at round r (1, 2, 3):
    seed = 42 + i + (r - 1) * 1000
    Round 1: 42 + i
    Round 2: 1042 + i
    Round 3: 2042 + i

Seed is assigned strictly prior to action selection and is independent of the action.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.evaluator import SEED_BASE
from evaluation.policies.feature_aware_heuristic import StrongFeatureAwareHeuristic
from evaluation.sequential.state import SequentialCaseState
from evaluation.sequential.transition import transition_case_state, MAX_HORIZON_ROUNDS
from simulator.policies import DeterministicBaselinePolicy, MLExpectedValuePolicy
from simulator.recovery_simulator import simulate_action


class SequentialPolicyEvaluator:
    """
    Orchestrates the sequential evaluation of policies over a test population.
    """

    def __init__(self, seed_base: int = SEED_BASE, max_rounds: int = MAX_HORIZON_ROUNDS):
        self.seed_base = seed_base
        self.max_rounds = max_rounds

    def evaluate_policy(
        self,
        test_df: pd.DataFrame,
        policy_fn: Callable[[pd.DataFrame], List[str]],
        policy_name: str,
    ) -> List[SequentialCaseState]:
        """
        Runs sequential evaluation across all test cases up to max_rounds.

        Parameters
        ----------
        test_df : pd.DataFrame
            Initial 1,000 test cases.
        policy_fn : Callable[[pd.DataFrame], List[str]]
            Function taking a DataFrame of active cases and returning selected actions.
        policy_name : str
            Human-readable name of the policy.

        Returns
        -------
        List[SequentialCaseState]
            List of final state objects for all test cases.
        """
        n = len(test_df)
        states = [SequentialCaseState.from_row(test_df.iloc[i], i) for i in range(n)]

        for r in range(1, self.max_rounds + 1):
            # Identify active cases for round r
            active_indices = [i for i, s in enumerate(states) if not s.is_terminated]
            if not active_indices:
                break

            # Build DataFrame of active cases
            active_records = [states[i].to_record_dict() for i in active_indices]
            active_df = pd.DataFrame(active_records)

            # Policy selects actions for all active cases
            chosen_actions = policy_fn(active_df)

            # Execute simulation and state transitions
            for idx_in_active, case_idx in enumerate(active_indices):
                state = states[case_idx]
                action = chosen_actions[idx_in_active]

                # Deterministic seed formula: seed_base + case_index + (r - 1) * 1000
                seed = self.seed_base + case_idx + (r - 1) * 1000

                sim_outcome = simulate_action(state.to_record_dict(), action, seed=seed)
                transition_case_state(state, action, sim_outcome)

        return states

    def evaluate_baseline(self, test_df: pd.DataFrame) -> List[SequentialCaseState]:
        policy = DeterministicBaselinePolicy()
        return self.evaluate_policy(test_df, policy.select_actions_batch, "Deterministic Baseline")

    def evaluate_heuristic(self, test_df: pd.DataFrame) -> List[SequentialCaseState]:
        policy = StrongFeatureAwareHeuristic()
        return self.evaluate_policy(test_df, policy.select_actions_batch, "Strong Feature-Aware Heuristic")

    def evaluate_recoveros(self, test_df: pd.DataFrame, model: Any) -> List[SequentialCaseState]:
        policy = MLExpectedValuePolicy(model, guardrails_enabled=True)
        return self.evaluate_policy(test_df, policy.select_actions_batch, "RecoverOS ML Policy")
