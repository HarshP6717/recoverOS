"""
Tests for API Request Validation.
"""

import pytest
from fastapi.testclient import TestClient


def test_diagnose_valid_request(client: TestClient, sample_diagnosis_payload: dict):
    """Verify valid diagnosis request succeeds with 200 OK."""
    response = client.post("/v1/recover/diagnose", json=sample_diagnosis_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["transaction_id"] == sample_diagnosis_payload["transaction_id"]
    assert "recommended_action" in data
    assert len(data["candidate_evaluations"]) == 7


def test_diagnose_rejects_negative_amount(client: TestClient, sample_diagnosis_payload: dict):
    """Verify negative amount is rejected with 422 Unprocessable Entity."""
    payload = dict(sample_diagnosis_payload)
    payload["amount"] = -500.0
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 422


def test_diagnose_rejects_zero_amount(client: TestClient, sample_diagnosis_payload: dict):
    """Verify zero amount is rejected with 422 Unprocessable Entity."""
    payload = dict(sample_diagnosis_payload)
    payload["amount"] = 0.0
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 422


def test_diagnose_rejects_missing_transaction_id(client: TestClient, sample_diagnosis_payload: dict):
    """Verify missing transaction_id is rejected with 422."""
    payload = dict(sample_diagnosis_payload)
    del payload["transaction_id"]
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 422


def test_diagnose_rejects_invalid_attempt_number(client: TestClient, sample_diagnosis_payload: dict):
    """Verify attempt_number < 1 is rejected with 422."""
    payload = dict(sample_diagnosis_payload)
    payload["attempt_number"] = 0
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 422


def test_diagnose_rejects_negative_days_overdue(client: TestClient, sample_diagnosis_payload: dict):
    """Verify negative days_overdue is rejected with 422."""
    payload = dict(sample_diagnosis_payload)
    payload["days_overdue"] = -5
    response = client.post("/v1/recover/diagnose", json=payload)
    assert response.status_code == 422


def test_decision_endpoint_valid_request(client: TestClient, sample_diagnosis_payload: dict):
    """Verify valid decision request persists and returns 201 Created."""
    response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["event_id"].startswith("evt_")
    assert data["audit_persisted"] is True
    assert data["selected_action"] in [
        "retry_now",
        "retry_later",
        "send_reminder",
        "payment_method_update",
        "recovery_link",
        "escalate_human",
        "stop",
    ]
