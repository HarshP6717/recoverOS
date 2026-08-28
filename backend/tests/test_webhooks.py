"""
Tests for Razorpay Webhook Ingestion, Signature Verification, and Atomic Idempotency.
"""

import json
import pytest
from fastapi.testclient import TestClient
from backend.tests.conftest import generate_razorpay_signature


def test_webhook_valid_signature_and_processing(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify valid HMAC-SHA256 signature leads to successful webhook processing
    and decision orchestration.
    """
    raw_body = json.dumps(sample_razorpay_webhook_payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)

    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": sample_razorpay_webhook_payload["event_id"],
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert data["webhook_event_id"] == sample_razorpay_webhook_payload["event_id"]
    assert data["decision"]["amount"] == 1499.0  # 149900 paise converted to ₹1,499.00
    assert data["decision"]["selected_action"] in [
        "retry_now",
        "retry_later",
        "send_reminder",
        "payment_method_update",
        "recovery_link",
        "escalate_human",
        "stop",
    ]


def test_webhook_invalid_signature_rejected(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify invalid HMAC signature is rejected with HTTP 401 Unauthorized.
    """
    raw_body = json.dumps(sample_razorpay_webhook_payload).encode("utf-8")
    headers = {
        "X-Razorpay-Signature": "invalid_fake_signature_999",
        "X-Razorpay-Event-Id": "evt_rzp_fake_001",
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 401
    assert "Invalid or missing X-Razorpay-Signature" in response.json()["detail"]


def test_webhook_missing_signature_rejected(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify missing signature header is rejected with HTTP 401.
    """
    raw_body = json.dumps(sample_razorpay_webhook_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 401


def test_webhook_atomic_idempotency_duplicate_ignored(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify that delivering the identical webhook event twice triggers atomic idempotency
    and ignores the second delivery without duplicate event creation.
    """
    payload = dict(sample_razorpay_webhook_payload)
    payload["event_id"] = "evt_rzp_unique_idempotency_123"
    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)

    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": payload["event_id"],
        "Content-Type": "application/json",
    }

    # 1. First delivery -> processed
    resp1 = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "processed"
    event_id_1 = resp1.json()["event_id"]

    # 2. Second delivery -> duplicate_ignored
    resp2 = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate_ignored"
    assert resp2.json()["decision"] is None


def test_webhook_unsupported_non_failure_event(client: TestClient):
    """
    Verify unsupported non-failure events (e.g. payment.authorized) are gracefully ignored.
    """
    payload = {
        "entity": "event",
        "account_id": "acc_test",
        "event": "payment.authorized",
        "event_id": "evt_auth_001",
        "payload": {},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored_unsupported_event"
    assert "payment.authorized" in data["message"]
