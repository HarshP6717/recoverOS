"""
RecoverOS Phase 3 Step 2F — Production Hardening Regression Tests.

Covers all 14 required regression tests for P0/P1/P2 audit findings.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import threading
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from backend.app.models.database import Base, init_db, RecoveryJourneyModel, ProcessedWebhookModel
from backend.app.services.journey_service import JourneyService, MAX_HORIZON_ROUNDS
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.razorpay_client import RazorpayTestClient
from backend.app.repositories.event_repository import (
    reserve_webhook_event_atomic,
    mark_webhook_processed,
)
from backend.tests.conftest import generate_razorpay_signature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import tempfile
import os

@pytest.fixture
def in_memory_db():
    """Isolated SQLite DB for each test, using a temp file to support cross-thread tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def journey_svc():
    return JourneyService()


@pytest.fixture
def recon_svc():
    return ReconciliationService()


def _make_journey(db, tx_id, status="IN_PROGRESS", current_round=1, amount=1000.0):
    """Helper: create a journey and optionally force it to a given status."""
    svc = JourneyService()
    journey = svc.get_or_create_journey(
        db=db,
        transaction_id=tx_id,
        amount=amount,
        payment_method="card",
        failure_type="insufficient_funds",
    )
    if status != "IN_PROGRESS":
        journey.status = status
        journey.termination_reason = status
        if status == "STOPPED":
            journey.termination_reason = "STOP_ACTION"
        elif status == "ESCALATED":
            journey.termination_reason = "ESCALATE_ACTION"
        elif status == "EXHAUSTED":
            journey.termination_reason = "MAX_ROUNDS_REACHED"
        db.commit()
        db.refresh(journey)
    if current_round != 1:
        journey.current_round = current_round
        db.commit()
        db.refresh(journey)
    return journey


def _settlement_payload(tx_id: str, amount_paise: int = 100000):
    """Build a minimal payment_link.paid settlement payload."""
    return {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_settle_test",
                    "amount": amount_paise,
                    "reference_id": tx_id,
                    "notes": {"transaction_id": tx_id},
                }
            },
            "payment": {"entity": {"id": "pay_settle_01", "amount": amount_paise}},
        },
    }


# ---------------------------------------------------------------------------
# TEST 1: Concurrent Journey Creation (P0-1)
# ---------------------------------------------------------------------------

def test_concurrent_journey_creation_atomic(in_memory_db):
    """
    Two threads attempting to create a journey for the same transaction_id
    must produce exactly one journey, not two. The UNIQUE constraint + IntegrityError
    retry in JourneyService.get_or_create_journey handles the race.
    """
    db = in_memory_db
    svc = JourneyService()
    results = []
    errors = []

    # Use a separate engine+session per thread (SQLite WAL is not safe across shared sessions)
    engine = db.get_bind()
    ThreadSession = sessionmaker(bind=engine)

    def create_journey():
        thread_db = ThreadSession()
        try:
            j = svc.get_or_create_journey(
                db=thread_db,
                transaction_id="tx_concurrent_001",
                amount=999.0,
                payment_method="upi",
                failure_type="bank_timeout",
            )
            results.append(j.journey_id)
        except Exception as e:
            errors.append(str(e))
        finally:
            thread_db.close()

    t1 = threading.Thread(target=create_journey)
    t2 = threading.Thread(target=create_journey)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # No unhandled errors
    assert len(errors) == 0, f"Unexpected errors: {errors}"
    # Exactly one journey in the DB
    all_db = db.get_bind()
    count_session = sessionmaker(bind=all_db)()
    count = count_session.query(RecoveryJourneyModel).filter(
        RecoveryJourneyModel.transaction_id == "tx_concurrent_001"
    ).count()
    count_session.close()
    assert count == 1, f"Expected 1 journey, got {count}"
    # Both threads should have gotten the same journey_id
    assert len(set(results)) == 1, f"Got different journey IDs: {results}"


# ---------------------------------------------------------------------------
# TEST 2: STOPPED + Settlement → terminal_state_conflict (P0-2)
# ---------------------------------------------------------------------------

def test_stopped_journey_settlement_returns_conflict(in_memory_db, recon_svc):
    """
    A settlement webhook for a STOPPED journey must return terminal_state_conflict.
    The journey must NOT be auto-overridden to RECOVERED.
    """
    journey = _make_journey(in_memory_db, "tx_stopped_001", status="STOPPED")
    payload = _settlement_payload("tx_stopped_001")

    result = recon_svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment_link.paid",
        payload=payload,
    )

    assert result.status == "terminal_state_conflict", f"Expected conflict, got {result.status}"
    # Journey must remain STOPPED
    in_memory_db.refresh(journey)
    assert journey.status == "STOPPED", f"Journey should remain STOPPED, got {journey.status}"


# ---------------------------------------------------------------------------
# TEST 3: ESCALATED + Settlement → terminal_state_conflict (P0-2)
# ---------------------------------------------------------------------------

def test_escalated_journey_settlement_returns_conflict(in_memory_db, recon_svc):
    """
    A settlement webhook for an ESCALATED journey must return terminal_state_conflict.
    """
    journey = _make_journey(in_memory_db, "tx_escalated_001", status="ESCALATED")
    payload = _settlement_payload("tx_escalated_001")

    result = recon_svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment_link.paid",
        payload=payload,
    )

    assert result.status == "terminal_state_conflict"
    in_memory_db.refresh(journey)
    assert journey.status == "ESCALATED", f"Journey should remain ESCALATED, got {journey.status}"


# ---------------------------------------------------------------------------
# TEST 4: EXHAUSTED + Settlement → RECOVERED via canonical path (P1-8)
# ---------------------------------------------------------------------------

def test_exhausted_journey_accepts_late_settlement(in_memory_db, recon_svc):
    """
    A settlement on an EXHAUSTED journey (customer paid after all rounds were tried)
    is a legitimate late payment. Must be accepted via mark_recovered_from_exhausted
    and journey must become RECOVERED with termination_reason=LATE_SETTLEMENT_AFTER_EXHAUSTION.
    """
    journey = _make_journey(in_memory_db, "tx_exhausted_001", status="EXHAUSTED", amount=999.0)
    payload = _settlement_payload("tx_exhausted_001", amount_paise=99900)

    result = recon_svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment_link.paid",
        payload=payload,
    )

    assert result.status == "reconciled", f"Expected reconciled, got {result.status}"
    in_memory_db.refresh(journey)
    assert journey.status == "RECOVERED"
    assert journey.termination_reason == "LATE_SETTLEMENT_AFTER_EXHAUSTION"
    assert journey.recovered_amount == pytest.approx(999.0, rel=1e-2)


# ---------------------------------------------------------------------------
# TEST 5: payment_link.cancelled is NOT a settlement event (P2-6)
# ---------------------------------------------------------------------------

def test_payment_link_cancelled_is_not_settlement():
    """
    payment_link.cancelled must NOT be in SETTLEMENT_EVENTS.
    Routing it as a settlement would mark journeys RECOVERED upon cancellation.
    """
    from backend.app.services.razorpay_adapter import RazorpayAdapter
    adapter = RazorpayAdapter.__new__(RazorpayAdapter)
    assert "payment_link.cancelled" not in RazorpayAdapter.SETTLEMENT_EVENTS, (
        "payment_link.cancelled should never be in SETTLEMENT_EVENTS"
    )
    # Cancellations are either unsupported or NEUTRAL events — not settlement triggers
    assert not adapter.is_settlement_event("payment_link.cancelled")


def test_payment_link_cancelled_cannot_reconcile_journey(in_memory_db, recon_svc):
    """
    Even if payment_link.cancelled somehow arrives at reconcile_settlement,
    it should either be unmatched or conflict — never mark a journey RECOVERED.
    """
    journey = _make_journey(in_memory_db, "tx_cancel_route_001", status="IN_PROGRESS")
    # Simulate a cancellation payload that references our journey
    payload = {
        "event": "payment_link.cancelled",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_cancel_test",
                    "amount": 100000,
                    "reference_id": "tx_cancel_route_001",
                    "notes": {"transaction_id": "tx_cancel_route_001"},
                }
            },
        },
    }
    # Directly calling reconcile_settlement with a cancellation event should not RECOVER
    result = recon_svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment_link.cancelled",
        payload=payload,
    )
    in_memory_db.refresh(journey)
    # Journey must NOT have been auto-recovered by a cancellation
    assert journey.status != "RECOVERED", (
        "Journey should not be RECOVERED after a payment_link.cancelled event"
    )


# ---------------------------------------------------------------------------
# TEST 6: Deterministic Simulation IDs (P2-2)
# ---------------------------------------------------------------------------

def test_payment_link_simulation_id_is_deterministic():
    """
    Offline simulation IDs must be deterministic across calls with identical inputs.
    Python hash() is PYTHONHASHSEED-randomized; we use hashlib.sha256 instead.
    """
    client = RazorpayTestClient(live_execution=False)
    result1 = client.create_payment_link(
        amount=999.0,
        customer_id="cust_det_01",
        description="det_test",
        reference_id="ref_det_01",
    )
    result2 = client.create_payment_link(
        amount=999.0,
        customer_id="cust_det_01",
        description="det_test",
        reference_id="ref_det_01",
    )
    assert result1["id"] == result2["id"], (
        f"Simulation IDs should be deterministic: {result1['id']} != {result2['id']}"
    )
    assert result1["short_url"] == result2["short_url"]

    # Verify it's actually using sha256 (not hash())
    expected_hash = hashlib.sha256("ref_det_01_cust_det_01".encode()).hexdigest()[:8]
    assert result1["id"] == f"plink_test_{expected_hash}", (
        f"ID should use sha256: expected plink_test_{expected_hash}, got {result1['id']}"
    )


# ---------------------------------------------------------------------------
# TEST 7: Two-Phase Webhook Lifecycle — RESERVED → PROCESSED (P1-1)
# ---------------------------------------------------------------------------

def test_webhook_lifecycle_reserved_then_processed(in_memory_db):
    """
    After reserve_webhook_event_atomic, webhook_status must be RESERVED.
    After mark_webhook_processed, webhook_status must be PROCESSED.
    If a process crashes between reserve and process, the record remains RESERVED.
    """
    event_id = "evt_lifecycle_test_001"

    # Phase 1: Reserve
    reserved = reserve_webhook_event_atomic(in_memory_db, event_id, "payment.failed")
    assert reserved is True

    record = in_memory_db.query(ProcessedWebhookModel).filter(
        ProcessedWebhookModel.webhook_event_id == event_id
    ).first()
    assert record is not None
    assert record.webhook_status == "RESERVED", (
        f"Expected RESERVED after reservation, got {record.webhook_status}"
    )

    # Simulate crash: do NOT call mark_webhook_processed → status remains RESERVED
    # This is detectable for operational alerting.
    in_memory_db.refresh(record)
    assert record.webhook_status == "RESERVED"

    # Phase 2: Complete (mark PROCESSED)
    mark_webhook_processed(in_memory_db, event_id, recovery_event_id="evt_recoveros_001")
    in_memory_db.refresh(record)
    assert record.webhook_status == "PROCESSED"
    assert record.recovery_event_id == "evt_recoveros_001"


def test_webhook_duplicate_delivery_rejected_atomically(in_memory_db):
    """
    A second delivery of the same webhook_event_id must be rejected by the UNIQUE
    constraint regardless of the current webhook_status (RESERVED or PROCESSED).
    """
    event_id = "evt_dup_test_001"
    r1 = reserve_webhook_event_atomic(in_memory_db, event_id, "payment.failed")
    assert r1 is True

    # Second delivery — must be rejected
    r2 = reserve_webhook_event_atomic(in_memory_db, event_id, "payment.failed")
    assert r2 is False


# ---------------------------------------------------------------------------
# TEST 8: Razorpay Cancellation Timeout (P1-2)
# ---------------------------------------------------------------------------

def test_cancellation_failure_sets_cancellation_pending(in_memory_db):
    """
    If Razorpay payment link cancellation fails AFTER the journey is committed as RECOVERED,
    the result must have cancellation_pending=True (not silently swallowed).
    The journey's DB state (RECOVERED) must remain correct.
    """
    journey = _make_journey(in_memory_db, "tx_cancel_fail_001", status="IN_PROGRESS", amount=1500.0)
    # Give it an active payment link so the cancellation is attempted
    journey.active_payment_link_id = "plink_competing_active"
    in_memory_db.commit()
    in_memory_db.refresh(journey)

    # Mock Razorpay client to raise on cancellation
    mock_rzp = MagicMock()
    mock_rzp.cancel_payment_link.side_effect = Exception("Connection timeout")

    recon_svc = ReconciliationService(razorpay_client=mock_rzp)
    payload = _settlement_payload("tx_cancel_fail_001", amount_paise=150000)

    result = recon_svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment_link.paid",
        payload=payload,
    )

    assert result.status == "reconciled", f"Expected reconciled, got {result.status}"
    assert result.cancellation_pending is True, "cancellation_pending should be True on failure"

    # Journey DB state should be RECOVERED (correct despite Razorpay failure)
    in_memory_db.refresh(journey)
    assert journey.status == "RECOVERED"


# ---------------------------------------------------------------------------
# TEST 9: Payment Link Creation Idempotency Check (P1-3)
# ---------------------------------------------------------------------------

def test_payment_link_simulation_idempotency_same_reference():
    """
    Calling create_payment_link twice with the same reference_id returns the same
    deterministic ID. This simulates the idempotency behavior expected when a timeout
    occurs after Razorpay accepted the first request.
    """
    client = RazorpayTestClient(live_execution=False)
    r1 = client.create_payment_link(
        amount=2000.0,
        customer_id="cust_idem_01",
        description="idem_test",
        reference_id="ref_idem_unique_001",
    )
    r2 = client.create_payment_link(
        amount=2000.0,
        customer_id="cust_idem_01",
        description="idem_test",
        reference_id="ref_idem_unique_001",
    )
    # Same reference_id → same deterministic ID (no duplicate link)
    assert r1["id"] == r2["id"], (
        f"Duplicate creation check: expected same ID, got {r1['id']} vs {r2['id']}"
    )


# ---------------------------------------------------------------------------
# TEST 10: Real DB Health Check (P1-5)
# ---------------------------------------------------------------------------

def test_health_check_verifies_db_connectivity(client: TestClient):
    """
    /health must return database status from a real DB query (SELECT 1).
    When DB is accessible, must return status=healthy.
    """
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "sqlite_connected" in data["database"]
    assert "version" in data


def test_health_check_returns_unhealthy_on_db_failure():
    """
    /health must return 503 unhealthy when DB is inaccessible.
    We patch SessionLocal.execute to raise.
    """
    from backend.app.main import app
    from sqlalchemy.orm import Session

    test_client = TestClient(app, raise_server_exceptions=False)
    with patch("backend.app.main.SessionLocal") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.execute.side_effect = Exception("DB connection failed")
        mock_session_cls.return_value = mock_session

        response = test_client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# TEST 11: Round-3 Auto-Exhaustion (P1-6)
# ---------------------------------------------------------------------------

def test_fourth_failure_exhausts_journey(in_memory_db):
    """
    A journey at round 3 receiving a new failure webhook must be auto-exhausted
    rather than processing a 4th recovery action.
    """
    from backend.app.services.recovery_orchestrator import RecoveryOrchestrator
    from backend.app.schemas.recovery import DecisionRequest

    journey = _make_journey(in_memory_db, "tx_round3_001", current_round=MAX_HORIZON_ROUNDS)
    assert journey.current_round == MAX_HORIZON_ROUNDS

    orchestrator = RecoveryOrchestrator()
    request = DecisionRequest(
        transaction_id="tx_round3_001",
        customer_id="cust_r3",
        subscription_id="sub_r3",
        amount=999.0,
        payment_method="card",
        failure_type="insufficient_funds",
        attempt_number=4,
    )

    result = orchestrator.process_recovery(db=in_memory_db, request=request)
    assert result.status == "EXHAUSTED", f"Expected EXHAUSTED, got {result.status}"
    in_memory_db.refresh(journey)
    assert journey.status == "EXHAUSTED"


# ---------------------------------------------------------------------------
# TEST 12: Stale Webhook Replay Rejection (P1-7)
# ---------------------------------------------------------------------------

def test_stale_webhook_rejected_by_replay_protection(client: TestClient):
    """
    A webhook with created_at older than WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS must
    be rejected with HTTP 400. Current tolerance is 300 seconds (5 minutes).
    """
    from backend.tests.conftest import generate_razorpay_signature
    stale_payload = {
        "entity": "event",
        "event": "payment.failed",
        "event_id": "evt_stale_replay_test_001",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_stale_001",
                    "amount": 100000,
                    "method": "card",
                    "error_code": "INSUFFICIENT_FUNDS",
                }
            }
        },
        "created_at": 1000000000,  # Year 2001 — definitely stale
    }
    raw_body = json.dumps(stale_payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    response = client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": stale_payload["event_id"],
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "stale" in detail.lower() or "tolerance" in detail.lower()


def test_fresh_webhook_not_rejected_by_replay_protection(client: TestClient):
    """
    A webhook with a current created_at must NOT be rejected by replay protection.
    """
    from backend.tests.conftest import generate_razorpay_signature
    fresh_payload = {
        "entity": "event",
        "event": "payment.failed",
        "event_id": f"evt_fresh_replay_test_{int(time.time())}",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_fresh_{int(time.time())}",
                    "amount": 50000,
                    "method": "upi",
                    "error_code": "BANK_TIMEOUT",
                }
            }
        },
        "created_at": int(time.time()),  # current
    }
    raw_body = json.dumps(fresh_payload).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    response = client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": fresh_payload["event_id"],
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


def test_webhook_without_created_at_passes_replay_check(client: TestClient):
    """
    A webhook without created_at (not provided by Razorpay in some test events)
    must NOT be rejected. Replay check is skipped when field is absent.
    """
    from backend.tests.conftest import generate_razorpay_signature
    payload_no_ts = {
        "entity": "event",
        "event": "payment.failed",
        "event_id": f"evt_no_ts_{int(time.time())}",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_no_ts_{int(time.time())}",
                    "amount": 50000,
                    "method": "card",
                }
            }
        },
        # No created_at field
    }
    raw_body = json.dumps(payload_no_ts).encode("utf-8")
    signature = generate_razorpay_signature(raw_body)
    response = client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": payload_no_ts["event_id"],
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# TEST 13: INF / NaN / Negative Settlement Amounts (P2-7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_paise,description", [
    ("Infinity", "positive infinity string"),
    ("-Infinity", "negative infinity string"),
    ("NaN", "NaN string"),
    ("-100", "negative paise"),
    ("0", "zero paise"),
])
def test_invalid_settlement_amount_rejected(in_memory_db, description, raw_paise):
    """
    Settlement payloads with NaN, Infinity, negative, or zero amounts must be
    rejected with status=invalid_amount rather than stored or processed.
    """
    _make_journey(in_memory_db, f"tx_invalid_amt_{raw_paise.replace('-','n')}")
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_invalid_001",
                    "amount": raw_paise,
                    "notes": {"transaction_id": f"tx_invalid_amt_{raw_paise.replace('-','n')}"},
                }
            }
        },
    }
    svc = ReconciliationService()
    result = svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment.captured",
        payload=payload,
    )
    assert result.status == "invalid_amount", (
        f"Expected invalid_amount for {description}, got {result.status}: {result.message}"
    )


def test_inf_amount_from_float_conversion_rejected(in_memory_db):
    """
    float('INF') / 100 = inf — must be rejected, not stored.
    """
    _make_journey(in_memory_db, "tx_inf_float_001")
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_inf_001",
                    "amount": float("inf") * 100,
                    "notes": {"transaction_id": "tx_inf_float_001"},
                }
            }
        },
    }
    svc = ReconciliationService()
    result = svc.reconcile_settlement(
        db=in_memory_db,
        event_type="payment.captured",
        payload=payload,
    )
    assert result.status == "invalid_amount"


# ---------------------------------------------------------------------------
# TEST 14: Duplicate Concurrent Webhook Delivery (P1-1 idempotency)
# ---------------------------------------------------------------------------

def test_duplicate_concurrent_webhook_delivery_idempotent(in_memory_db):
    """
    Two concurrent deliveries of the same webhook_event_id must result in
    exactly one successful reservation (True) and one rejection (False).
    The UNIQUE constraint is the atomic guard.
    """
    event_id = "evt_concurrent_dup_001"
    results = []
    errors = []

    engine = in_memory_db.get_bind()
    ThreadSession = sessionmaker(bind=engine)

    def try_reserve():
        thread_db = ThreadSession()
        try:
            r = reserve_webhook_event_atomic(thread_db, event_id, "payment.failed")
            results.append(r)
        except Exception as e:
            errors.append(str(e))
        finally:
            thread_db.close()

    t1 = threading.Thread(target=try_reserve)
    t2 = threading.Thread(target=try_reserve)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(errors) == 0, f"Unexpected errors: {errors}"
    # Exactly one True and one False
    assert results.count(True) == 1, f"Expected exactly one reservation success: {results}"
    assert results.count(False) == 1, f"Expected exactly one duplicate rejection: {results}"
