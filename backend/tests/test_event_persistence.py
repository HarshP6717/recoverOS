"""
Tests for Recovery Event Audit Persistence and Retrieval.
"""

import pytest
from fastapi.testclient import TestClient


def test_event_persistence_and_retrieval(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify that an orchestrated decision event is stored in the audit ledger
    and retrievable via GET /v1/recover/events/{event_id}.
    """
    # 1. Create decision event
    post_resp = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
    assert post_resp.status_code == 201
    post_data = post_resp.json()
    event_id = post_data["event_id"]

    # 2. Retrieve event by ID
    get_resp = client.get(f"/v1/recover/events/{event_id}")
    assert get_resp.status_code == 200
    get_data = get_resp.json()

    assert get_data["event_id"] == event_id
    assert get_data["transaction_id"] == sample_diagnosis_payload["transaction_id"]
    assert get_data["amount"] == sample_diagnosis_payload["amount"]
    assert get_data["selected_action"] == post_data["selected_action"]
    assert get_data["decision_status"] == post_data["decision_status"]
    assert len(get_data["candidate_evaluations"]) == 7
    assert get_data["raw_payload"] is not None


def test_event_retrieval_not_found(client: TestClient):
    """
    Verify requesting a non-existent event ID returns HTTP 404 Not Found.
    """
    response = client.get("/v1/recover/events/evt_non_existent_id_999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
