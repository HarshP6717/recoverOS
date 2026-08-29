import pytest
from backend.app.schemas.recovery import DiagnosisRequest, ActionCandidateEvaluation
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.razorpay_adapter import RazorpayAdapter

def test_razorpay_adapter_subscription_halted():
    adapter = RazorpayAdapter()
    
    payload = {
        "event": "subscription.halted",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_123",
                    "status": "halted"
                }
            }
        }
    }
    
    event_type, decision_request = adapter.normalize_webhook_payload(payload, "evt_1")
    assert event_type == "subscription.halted"
    assert decision_request.failure_type == "subscription_halted"


def test_razorpay_adapter_subscription_status_halted():
    adapter = RazorpayAdapter()
    
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_123",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed"
                }
            },
            "subscription": {
                "entity": {
                    "id": "sub_123",
                    "status": "halted"
                }
            }
        }
    }
    
    event_type, decision_request = adapter.normalize_webhook_payload(payload, "evt_2")
    assert event_type == "payment.failed"
    assert decision_request.failure_type == "subscription_halted"


def test_guardrails_subscription_halted_blocks_unsafe_actions():
    engine = GuardrailEngine()
    req = DiagnosisRequest(
        transaction_id="tx_1",
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=500.0,
        payment_method="card",
        failure_type="subscription_halted",
        attempt_number=1,
        days_overdue=1,
        previous_payment_count=0,
        previous_success_count=0,
        previous_failure_count=0,
        previous_recovery_count=0,
        customer_lifetime_value=0.0,
        contact_count=0,
        subscription_age_days=0,
        source="razorpay_webhook",
        external_event_id="evt_1"
    )
    
    unfiltered = {
        "retry_now": (0.8, 1.0, 400.0),
        "retry_later": (0.8, 1.0, 400.0),
        "recovery_link": (0.8, 1.5, 400.0),
        "payment_method_update": (0.9, 2.0, 450.0),
        "escalate_human": (0.9, 30.0, 420.0),
        "stop": (0.0, 0.0, 0.0)
    }
    
    evals, triggers = engine.evaluate_candidates(req, unfiltered)
    
    assert "G6_HALTED_SUBSCRIPTION_SAFETY" in triggers
    
    for e in evals:
        if e.action in {"retry_now", "retry_later", "recovery_link"}:
            assert not e.allowed
            assert "G6" in e.suppression_reason or "G2" in e.suppression_reason
        elif e.action in {"payment_method_update", "escalate_human", "stop"}:
            assert e.allowed
