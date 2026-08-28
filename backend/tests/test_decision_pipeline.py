"""
Tests for Decision Orchestration Pipeline and Candidate Action Scoring.
"""

import pytest
from fastapi.testclient import TestClient


def test_decision_pipeline_candidate_evaluations_structure(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify decision response contains all 7 candidate actions with complete
    probability, cost, ERV, and guardrail metadata.
    """
    response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
    assert response.status_code == 201
    data = response.json()

    candidates = data["candidate_evaluations"]
    assert len(candidates) == 7

    candidate_map = {c["action"]: c for c in candidates}
    expected_actions = {
        "retry_now",
        "retry_later",
        "send_reminder",
        "payment_method_update",
        "recovery_link",
        "escalate_human",
        "stop",
    }
    assert set(candidate_map.keys()) == expected_actions


def test_explicit_stop_candidate_representation(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify STOP is represented explicitly with:
    - predicted_recovery_probability = 0.0
    - action_cost = 0.0
    - predicted_erv = 0.0
    - allowed = True
    """
    response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
    assert response.status_code == 201
    data = response.json()

    stop_candidate = next(c for c in data["candidate_evaluations"] if c["action"] == "stop")
    assert stop_candidate["predicted_recovery_probability"] == 0.0
    assert stop_candidate["action_cost"] == 0.0
    assert stop_candidate["predicted_erv"] == 0.0
    assert stop_candidate["allowed"] is True
    assert stop_candidate["suppression_reason"] is None


def test_decision_pipeline_selects_highest_permitted_erv(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify the selected action matches the highest permitted predicted ERV.
    """
    response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
    assert response.status_code == 201
    data = response.json()

    allowed_candidates = [c for c in data["candidate_evaluations"] if c["allowed"]]
    best_candidate = max(allowed_candidates, key=lambda c: c["predicted_erv"])

    assert data["selected_action"] == best_candidate["action"]
    assert data["decision_status"] == "SUCCESS"
