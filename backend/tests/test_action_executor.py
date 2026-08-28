"""
Tests for Action Execution Simulator across all 7 supported recovery actions.
"""

from datetime import datetime, timezone
import pytest
from backend.app.schemas.recovery import DecisionResponse
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.razorpay_client import RazorpayTestClient


def make_dummy_decision(action: str, amount: float = 999.0) -> DecisionResponse:
    return DecisionResponse(
        event_id="evt_test_exec_001",
        transaction_id="tx_test_exec_001",
        customer_id="cust_test_exec_001",
        subscription_id="sub_test_exec_001",
        amount=amount,
        selected_action=action,
        decision_status="SUCCESS",
        decision_reason=f"Selected {action}",
        guardrails_triggered=[],
        candidate_evaluations=[],
        model_version="recovery_logreg_v1",
        timestamp=datetime.now(timezone.utc),
        audit_persisted=True,
    )


def test_simulate_stop_action():
    """Verify STOP execution formally halts dunning and sets STOPPED status."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("stop")
    result = simulator.execute_action(decision)

    assert result.execution_id.startswith("exec_")
    assert result.selected_action == "stop"
    assert result.execution_status == "STOPPED"
    assert result.simulated_response["dunning_halted"] is True
    assert result.simulated_response["recovery_status"] == "terminated"


def test_simulate_retry_now_action():
    """Verify retry_now simulation returns SIMULATED_RECOVERED or SIMULATED_FAILED with gateway payload."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("retry_now")
    result = simulator.execute_action(decision)

    assert result.execution_id.startswith("exec_")
    assert result.selected_action == "retry_now"
    assert result.execution_status in ["SIMULATED_RECOVERED", "SIMULATED_FAILED"]
    assert result.simulated_response["gateway"] == "razorpay_test_mode"
    assert "payment_id" in result.simulated_response


def test_simulate_retry_later_action():
    """Verify retry_later simulation schedules delayed dunning job."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("retry_later")
    result = simulator.execute_action(decision)

    assert result.selected_action == "retry_later"
    assert result.execution_status == "SIMULATED_PENDING"
    assert result.simulated_response["queue"] == "delayed_dunning_queue"
    assert "job_id" in result.simulated_response
    assert "scheduled_for" in result.simulated_response


def test_simulate_send_reminder_action():
    """Verify send_reminder simulation generates customer notification."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("send_reminder")
    result = simulator.execute_action(decision)

    assert result.selected_action == "send_reminder"
    assert result.execution_status == "SUCCESS"
    assert result.simulated_response["channel"] == "whatsapp_and_email"
    assert result.simulated_response["delivery_status"] == "delivered_simulated"


def test_simulate_payment_method_update_action():
    """Verify payment_method_update simulation generates secure update session URL."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("payment_method_update")
    result = simulator.execute_action(decision)

    assert result.selected_action == "payment_method_update"
    assert result.execution_status == "SUCCESS"
    assert "update_url" in result.simulated_response
    assert result.simulated_response["status"] == "active"


def test_simulate_recovery_link_action():
    """Verify recovery_link simulation creates hosted Razorpay checkout link."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("recovery_link")
    result = simulator.execute_action(decision)

    assert result.selected_action == "recovery_link"
    assert result.execution_status == "SUCCESS"
    assert result.simulated_response["entity"] == "payment_link"
    assert result.simulated_response["short_url"].startswith("https://rzp.io/i/")


def test_simulate_escalate_human_action():
    """Verify escalate_human simulation creates concierge recovery CS ticket."""
    simulator = ActionExecutionSimulator()
    decision = make_dummy_decision("escalate_human", amount=2499.0)
    result = simulator.execute_action(decision)

    assert result.selected_action == "escalate_human"
    assert result.execution_status == "SUCCESS"
    assert result.simulated_response["crm"] == "concierge_recovery_queue"
    assert result.simulated_response["priority"] == "P1_URGENT"


def test_deterministic_outcome_reproducibility():
    """Verify identical decision parameters yield identical simulated retry outcomes."""
    simulator = ActionExecutionSimulator()
    decision1 = make_dummy_decision("retry_now")
    decision2 = make_dummy_decision("retry_now")

    res1 = simulator.execute_action(decision1)
    res2 = simulator.execute_action(decision2)

    assert res1.execution_status == res2.execution_status
    assert res1.simulated_response["authorization_code"] == res2.simulated_response["authorization_code"]


def test_action_executor_failure_safe_handling():
    """Verify simulated gateway failure produces EXECUTION_FAILED without crashing."""
    failing_client = RazorpayTestClient(simulate_gateway_down=True)
    simulator = ActionExecutionSimulator(razorpay_client=failing_client)

    decision = make_dummy_decision("recovery_link")
    result = simulator.execute_action(decision)

    assert result.execution_status == "EXECUTION_FAILED"
    assert result.error_code == "GATEWAY_UNAVAILABLE"
    assert "gateway is unavailable" in result.error_message
