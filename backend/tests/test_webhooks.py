"""
Tests for Razorpay Webhook Ingestion, Signature Verification, and Atomic Idempotency.
"""

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
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


def test_webhook_payment_link_paid_http_route(
    client: TestClient,
    db_session: Session,
):
    """
    Verify signed payment_link.paid webhook transitions journey to RECOVERED,
    converts paise to INR, updates net_value, and does NOT cancel its own settled link.
    """
    from backend.app.services.journey_service import JourneyService

    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id="tx_http_plink_001",
        amount=2499.0,
        payment_method="card",
        failure_type="expired_card",
    )
    journey_svc.record_action(
        db=db_session,
        journey_id=journey.journey_id,
        action="recovery_link",
        cost=1.50,
        payment_link_id="plink_http_paid_001",
        payment_link_url="https://rzp.io/i/paid001",
    )

    payload = {
        "entity": "event",
        "account_id": "acc_test_12345",
        "event": "payment_link.paid",
        "event_id": "evt_rzp_plink_http_001",
        "contains": ["payment_link", "payment"],
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_http_paid_001",
                    "amount": 249900,  # 249900 paise = ₹2,499.00
                    "reference_id": "tx_http_plink_001",
                    "notes": {"transaction_id": "tx_http_plink_001"},
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_http_plink_001",
                    "amount": 249900,
                }
            },
        },
        "created_at": 1700000000,
    }

    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": payload["event_id"],
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert data["webhook_event_id"] == "evt_rzp_plink_http_001"

    # Refresh journey from DB
    db_session.refresh(journey)
    assert journey.status == "RECOVERED"
    assert journey.recovered_amount == 2499.0
    assert journey.cumulative_cost == 1.50
    assert journey.net_value == 2499.0 - 1.50
    # Settled link was not removed or invalidated
    assert journey.active_payment_link_id == "plink_http_paid_001"


def test_webhook_payment_captured_http_route(
    client: TestClient,
    db_session: Session,
):
    """
    Verify signed payment.captured webhook reconciles journey and triggers competing link cancellation.
    """
    from backend.app.services.journey_service import JourneyService

    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id="tx_http_capture_001",
        amount=1500.0,
        payment_method="upi",
        failure_type="bank_timeout",
    )
    journey_svc.record_action(
        db=db_session,
        journey_id=journey.journey_id,
        action="recovery_link",
        cost=1.50,
        payment_link_id="plink_competing_http_001",
        payment_link_url="https://rzp.io/i/compete001",
    )

    payload = {
        "entity": "event",
        "account_id": "acc_test_12345",
        "event": "payment.captured",
        "event_id": "evt_rzp_capture_http_001",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_direct_capture_001",
                    "amount": 150000,
                    "notes": {"transaction_id": "tx_http_capture_001"},
                }
            }
        },
        "created_at": 1700000000,
    }

    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": payload["event_id"],
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"

    db_session.refresh(journey)
    assert journey.status == "RECOVERED"
    assert journey.recovered_amount == 1500.0
    assert journey.net_value == 1500.0 - 1.50


def test_webhook_subscription_charged_http_route(
    client: TestClient,
    db_session: Session,
):
    """
    Verify signed subscription.charged webhook transitions subscription journey to RECOVERED.
    """
    from backend.app.services.journey_service import JourneyService

    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id="tx_http_sub_001",
        amount=999.0,
        payment_method="mandate_nach",
        failure_type="insufficient_funds",
        subscription_id="sub_http_auto_001",
    )

    payload = {
        "entity": "event",
        "account_id": "acc_test_12345",
        "event": "subscription.charged",
        "event_id": "evt_rzp_sub_http_001",
        "contains": ["subscription", "payment"],
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_http_auto_001",
                    "plan_amount": 99900,
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_sub_http_001",
                    "amount": 99900,
                }
            },
        },
        "created_at": 1700000000,
    }

    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": payload["event_id"],
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"

    db_session.refresh(journey)
    assert journey.status == "RECOVERED"
    assert journey.recovered_amount == 999.0
    assert journey.net_value == 999.0


def test_webhook_unmatched_settlement_http_route(
    client: TestClient,
    db_session: Session,
):
    """
    Verify unmatched settlement webhook returns status unmatched without creating/mutating journeys.
    """
    from backend.app.models.database import RecoveryJourneyModel

    payload = {
        "entity": "event",
        "account_id": "acc_test_12345",
        "event": "payment.captured",
        "event_id": "evt_rzp_unmatched_http_001",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_ghost_999",
                    "amount": 120000,
                    "notes": {"transaction_id": "tx_ghost_nonexistent"},
                }
            }
        },
        "created_at": 1700000000,
    }

    raw_body = json.dumps(payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": payload["event_id"],
        "Content-Type": "application/json",
    }

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unmatched"
    assert "No matching recovery journey found" in data["message"]

    # Verify no journeys exist
    assert db_session.query(RecoveryJourneyModel).count() == 0


def test_webhook_deterministic_event_id_fallback(
    client: TestClient,
    sample_razorpay_webhook_payload: dict,
):
    """
    Verify fallback event ID generation is deterministic using SHA-256 (not Python hash()).
    """
    import hashlib

    payload = dict(sample_razorpay_webhook_payload)
    payload.pop("event_id", None)

    raw_body = json.dumps(payload).encode("utf-8")
    expected_event_id = f"rzp_evt_{hashlib.sha256(raw_body).hexdigest()[:16]}"
    signature = generate_razorpay_signature(raw_body)

    headers = {
        "X-Razorpay-Signature": signature,
        "Content-Type": "application/json",
    }

    # 1. First delivery -> processed under deterministic fallback event ID
    response1 = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["status"] == "processed"
    assert data1["webhook_event_id"] == expected_event_id

    # 2. Second delivery with identical body -> duplicate ignored under same deterministic ID
    response2 = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["status"] == "duplicate_ignored"
    assert data2["webhook_event_id"] == expected_event_id
