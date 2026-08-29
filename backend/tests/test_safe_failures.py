"""
Tests for Safe Failure Modes (Model unavailable, DB persistence error, unknown failure types).
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from backend.app.core.dependencies import get_decision_engine, get_event_service
from backend.app.main import app
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService


def test_model_unavailable_safe_fallback(client: TestClient, sample_diagnosis_payload: dict):
    """
    Verify that when the ML model is unavailable, the system safely falls back
    without triggering automated retries and marks decision_status as FALLBACK_SAFE.
    """
    from backend.app.services.diagnosis_engine import DiagnosisEngine
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.get_diagnosis.side_effect = Exception("Network Down")
    degraded_engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=mock_provider))
    app.dependency_overrides[get_decision_engine] = lambda: degraded_engine
    degraded_service = EventService(decision_engine=degraded_engine)
    app.dependency_overrides[get_event_service] = lambda: degraded_service

    try:
        response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
        assert response.status_code == 201
        data = response.json()
        assert data["decision_status"] == "FALLBACK_SAFE"
        assert "degraded state" in data["decision_reason"].lower()
        # Automated retries must be suppressed
        candidates = {c["action"]: c for c in data["candidate_evaluations"]}
        assert candidates["retry_now"]["allowed"] is False
        assert candidates["retry_later"]["allowed"] is False
    finally:
        app.dependency_overrides.clear()


def test_database_persistence_failure_halts_action(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify that if database persistence fails, the system returns HTTP 503
    and does not claim the decision was audited.
    """
    with patch(
        "backend.app.services.event_service.record_recovery_event",
        side_effect=Exception("Simulated SQLite Disk I/O Failure"),
    ):
        response = client.post("/v1/recover/decision", json=sample_diagnosis_payload)
        assert response.status_code == 503
        assert "Audit ledger persistence failed" in response.json()["detail"]


def test_unknown_failure_type_handled_safely(
    client: TestClient,
    sample_diagnosis_payload: dict,
):
    """
    Verify that an unmapped/unknown failure type processes safely.
    """
    payload = dict(sample_diagnosis_payload)
    payload["failure_type"] = "novel_unseen_bank_error_code_99"

    response = client.post("/v1/recover/decision", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["decision_status"] in ["SUCCESS", "FALLBACK_SAFE"]
    assert data["selected_action"] in [
        "retry_now",
        "retry_later",
        "send_reminder",
        "payment_method_update",
        "recovery_link",
        "escalate_human",
        "stop",
    ]
