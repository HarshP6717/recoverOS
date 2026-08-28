"""
RecoverOS Phase 2C Step 4 — Sequential State Transition Engine.

Defines the state transition rules for moving a payment failure case
from round r to round r+1 following action execution.

EXPLICIT STATE TRANSITION ASSUMPTIONS:
1. Contact Count:
   - 'send_reminder', 'recovery_link', 'payment_method_update', 'escalate_human'
     are customer-facing communications -> contact_count += 1.
   - 'retry_now', 'retry_later' are silent automated backend gateway attempts
     -> contact_count += 0.
2. Days Overdue Delay:
   - 'retry_now': immediate execution -> +0.5 days.
   - 'retry_later', 'send_reminder', 'recovery_link', 'payment_method_update':
     standard dunning grace interval -> +2.0 days.
3. Historical Counters:
   - Failed non-stop attempt: attempt_number += 1, previous_failure_count += 1,
     previous_payment_count += 1.
   - Successful recovery: previous_success_count += 1, previous_recovery_count += 1,
     previous_payment_count += 1, customer_lifetime_value += amount.
4. Termination Rules:
   - Successful recovery: immediate termination ('RECOVERED').
   - 'stop' action: immediate termination ('STOP_ACTION').
   - 'escalate_human' action: immediate automated termination ('ESCALATE_ACTION').
   - Failed round 3: horizon limit reached ('MAX_ROUNDS_REACHED').
"""

from __future__ import annotations

from typing import Any, Dict
from evaluation.sequential.state import SequentialCaseState
from simulator.recovery_simulator import ACTION_COSTS

# Customer-facing actions that increase contact fatigue
CUSTOMER_FACING_ACTIONS = frozenset([
    "send_reminder",
    "recovery_link",
    "payment_method_update",
    "escalate_human",
])

# Delay in days overdue added after a failed attempt
ACTION_DAYS_OVERDUE_INCREMENT: Dict[str, float] = {
    "retry_now": 0.5,
    "retry_later": 2.0,
    "send_reminder": 2.0,
    "recovery_link": 2.0,
    "payment_method_update": 2.0,
    "escalate_human": 2.0,
    "stop": 0.0,
}

MAX_HORIZON_ROUNDS = 3


def transition_case_state(
    state: SequentialCaseState,
    action: str,
    sim_outcome: Dict[str, Any],
) -> SequentialCaseState:
    """
    Applies the simulation outcome of action to the case state and transitions it.

    Parameters
    ----------
    state : SequentialCaseState
        Current case state before action outcome.
    action : str
        Action executed in current_round.
    sim_outcome : Dict[str, Any]
        Output dictionary from simulate_action(state.to_record_dict(), action, seed=seed).

    Returns
    -------
    SequentialCaseState
        The updated case state.
    """
    if state.is_terminated:
        return state

    cost = float(sim_outcome.get("action_cost", ACTION_COSTS.get(action, 0.0)))
    recovered = bool(sim_outcome.get("recovered", False))
    rec_amount = float(sim_outcome.get("recovered_amount", 0.0))

    state.cumulative_action_cost += cost
    state.action_history.append(action)
    state.round_outcomes.append({
        "round": state.current_round,
        "action": action,
        "recovered": recovered,
        "recovered_amount": rec_amount,
        "action_cost": cost,
        "net_value": sim_outcome.get("net_value", rec_amount - cost),
        "recovery_probability_gt": sim_outcome.get("recovery_probability", 0.0),
    })

    # Case 1: Recovery occurs
    if recovered:
        state.is_recovered = True
        state.is_terminated = True
        state.termination_reason = "RECOVERED"
        state.recovered_round = state.current_round
        state.cumulative_recovered_amount = state.amount
        state.cumulative_net_value = state.cumulative_recovered_amount - state.cumulative_action_cost

        state.previous_success_count += 1
        state.previous_recovery_count += 1
        state.previous_payment_count += 1
        state.customer_lifetime_value += state.amount
        state.previous_success_rate = state.previous_success_count / max(1, state.previous_payment_count)
        state.previous_recovery_rate = state.previous_recovery_count / max(1, state.previous_failure_count)
        return state

    # Case 2: STOP action chosen
    if action == "stop":
        state.is_recovered = False
        state.is_terminated = True
        state.termination_reason = "STOP_ACTION"
        state.cumulative_net_value = 0.0 - state.cumulative_action_cost
        return state

    # Case 3: ESCALATE_HUMAN action chosen (and failed)
    if action == "escalate_human":
        state.is_recovered = False
        state.is_terminated = True
        state.termination_reason = "ESCALATE_ACTION"
        state.cumulative_net_value = 0.0 - state.cumulative_action_cost
        return state

    # Case 4: Failed non-stop, non-escalate action
    state.cumulative_net_value = 0.0 - state.cumulative_action_cost
    state.attempt_number += 1
    state.previous_failure_count += 1
    state.previous_payment_count += 1
    state.previous_success_rate = state.previous_success_count / max(1, state.previous_payment_count)
    state.previous_recovery_rate = state.previous_recovery_count / max(1, state.previous_failure_count)

    # Increment contact count if action was customer facing
    if action in CUSTOMER_FACING_ACTIONS:
        state.contact_count += 1

    # Increment days overdue
    state.days_overdue += ACTION_DAYS_OVERDUE_INCREMENT.get(action, 2.0)

    # Check horizon limit
    if state.current_round >= MAX_HORIZON_ROUNDS:
        state.is_terminated = True
        state.termination_reason = "MAX_ROUNDS_REACHED"
    else:
        state.current_round += 1

    return state
