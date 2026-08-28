"""
Tests for Deterministic Guardrails Engine (G1, G2, G3, G4, G5).
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.services.guardrails import GuardrailEngine
from backend.app.schemas.recovery import DiagnosisRequest


def test_guardrail_g2_permanent_failure_retry_suppression(client: TestClient):
    """
    Verify G2 suppresses retry_now and retry_later on permanent failure 'expired_card'.
    """
    payload = {
        "transaction_id": "tx_g2_001",
        "customer_id": "cust_g2_001",
        "subscription_id": "sub_g2_001",
        "amount": 1499.0,
        "payment_method": "card",
        "failure_type": "expired_card",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 6,
        "previous_success_count": 6,
        "previous_failure_count": 0,
        "previous_recovery_count": 0,
        "customer_lifetime_value": 8994.0,
        "contact_count": 0,
    }
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()

    candidates = {c["action"]: c for c in data["candidate_evaluations"]}
    assert candidates["retry_now"]["allowed"] is False
    assert "permanent failure" in candidates["retry_now"]["suppression_reason"].lower()
    assert candidates["retry_later"]["allowed"] is False
    assert "permanent failure" in candidates["retry_later"]["suppression_reason"].lower()
    assert "G2_PERMANENT_FAILURE_RETRY_SUPPRESSION" in data["guardrails_triggered"]


def test_guardrail_g3_micro_amount_human_escalation(client: TestClient):
    """
    Verify G3 suppresses escalate_human (₹30) for invoice amount < ₹100.00.
    """
    payload = {
        "transaction_id": "tx_g3_001",
        "customer_id": "cust_g3_001",
        "subscription_id": "sub_g3_001",
        "amount": 49.0,  # Micro-amount < ₹100 in INR
        "payment_method": "upi",
        "failure_type": "customer_abandoned",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 1,
        "previous_success_count": 1,
        "previous_failure_count": 0,
        "previous_recovery_count": 0,
        "customer_lifetime_value": 49.0,
        "contact_count": 0,
    }
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()

    candidates = {c["action"]: c for c in data["candidate_evaluations"]}
    assert candidates["escalate_human"]["allowed"] is False
    assert "amount < ₹100" in candidates["escalate_human"]["suppression_reason"]
    assert "G3_MICRO_AMOUNT_HUMAN_SUPPRESSION" in data["guardrails_triggered"]


def test_guardrail_g4_customer_fatigue_cap(client: TestClient):
    """
    Verify G4 suppresses repeated retries and reminders when contact_count >= 5.
    """
    payload = {
        "transaction_id": "tx_g4_001",
        "customer_id": "cust_g4_001",
        "subscription_id": "sub_g4_001",
        "amount": 999.0,
        "payment_method": "upi",
        "failure_type": "insufficient_funds",
        "attempt_number": 3,
        "days_overdue": 15,
        "previous_payment_count": 6,
        "previous_success_count": 5,
        "previous_failure_count": 1,
        "previous_recovery_count": 1,
        "customer_lifetime_value": 4995.0,
        "contact_count": 5,  # Fatigue cap reached
    }
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 200
    data = response.json()

    candidates = {c["action"]: c for c in data["candidate_evaluations"]}
    assert candidates["retry_now"]["allowed"] is False
    assert candidates["retry_later"]["allowed"] is False
    assert candidates["send_reminder"]["allowed"] is False
    assert "G4_CUSTOMER_FATIGUE_CAP" in data["guardrails_triggered"]


def test_all_actions_suppressed_selects_stop():
    """
    Verify that when all active recovery candidates are suppressed,
    STOP is explicitly selected with SUPPRESSED_STOP status.
    """
    guardrail_engine = GuardrailEngine()
    request = DiagnosisRequest(
        transaction_id="tx_all_suppressed",
        customer_id="cust_001",
        subscription_id="sub_001",
        amount=50.0,
        payment_method="card",
        failure_type="hard_decline",  # Suppresses retry_now, retry_later
        attempt_number=5,             # Suppresses retries, send_reminder (G4)
        days_overdue=30,
        contact_count=5,              # G4
    )

    # Synthetic evaluations where all non-stop candidates have negative ERV or are suppressed
    unfiltered = {
        "retry_now": (0.01, 1.00, -0.50),
        "retry_later": (0.01, 1.00, -0.50),
        "send_reminder": (0.01, 0.50, -0.20),
        "payment_method_update": (0.01, 2.00, -1.50),  # ERV <= 0 (G1)
        "recovery_link": (0.01, 1.50, -1.00),          # ERV <= 0 (G1)
        "escalate_human": (0.01, 30.00, -29.00),       # ERV <= 0 (G1) and amount < 100 (G3)
        "stop": (0.00, 0.00, 0.00),
    }

    evaluations, triggered = guardrail_engine.evaluate_candidates(request, unfiltered)
    selected_action, status, reason = guardrail_engine.select_best_action(evaluations)

    assert selected_action == "stop"
    assert status == "SUPPRESSED_STOP"
    assert "STOP selected" in reason
