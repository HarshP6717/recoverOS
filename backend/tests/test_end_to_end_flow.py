"""
Tests for End-to-End Recovery Flow: Webhook -> Decision -> Execution -> Audit Record.
"""

import json
import pytest
from fastapi.testclient import TestClient
from backend.tests.conftest import generate_razorpay_signature


def test_end_to_end_webhook_decision_execution_flow(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify complete pipeline from webhook ingestion, HMAC verification, decision orchestration,
    action simulation, to full audit ledger record retrieval.
    """
    raw_body = json.dumps(sample_razorpay_webhook_payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": sample_razorpay_webhook_payload["event_id"],
        "Content-Type": "application/json",
    }

    # 1. Ingest Webhook
    resp = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert data["event_id"] is not None
    assert data["execution"] is not None
    event_id = data["event_id"]
    execution_id = data["execution"]["execution_id"]

    # 2. Verify Recovery Event Audit Log
    event_resp = client.get(f"/v1/recover/events/{event_id}")
    assert event_resp.status_code == 200
    event_data = event_resp.json()
    assert event_data["event_id"] == event_id
    assert event_data["amount"] == 1499.0
    assert len(event_data["executions"]) >= 1
    assert event_data["executions"][0]["execution_id"] == execution_id

    # 3. Verify Execution Audit Record
    exec_resp = client.get(f"/v1/recover/executions/{execution_id}")
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["execution_id"] == execution_id
    assert exec_data["event_id"] == event_id
    assert exec_data["selected_action"] in [
        "retry_now",
        "retry_later",
        "send_reminder",
        "payment_method_update",
        "recovery_link",
        "escalate_human",
        "stop",
    ]


def test_explicit_execute_endpoint(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify creating a decision without auto-execute, then explicitly executing
    via POST /v1/recover/execute.
    """
    # 1. Create decision with auto_execute=False
    decision_resp = client.post(
        "/v1/recover/decision?auto_execute=false",
        json=sample_diagnosis_payload,
    )
    assert decision_resp.status_code == 201
    decision_data = decision_resp.json()
    event_id = decision_data["event_id"]
    assert decision_data["execution"] is None

    # 2. Trigger explicit execution
    exec_resp = client.post("/v1/recover/execute", json={"event_id": event_id})
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["event_id"] == event_id
    assert exec_data["selected_action"] == decision_data["selected_action"]

    # 3. Retrieve event and verify execution is attached
    event_resp = client.get(f"/v1/recover/events/{event_id}")
    assert event_resp.status_code == 200
    assert len(event_resp.json()["executions"]) == 1


def test_execute_non_existent_event_returns_404(client: TestClient):
    """
    Verify triggering execution on a non-existent event ID returns 404.
    """
    resp = client.post("/v1/recover/execute", json={"event_id": "evt_does_not_exist_404"})
    assert resp.status_code == 404
