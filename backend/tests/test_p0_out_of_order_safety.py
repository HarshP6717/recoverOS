import pytest
import uuid
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models.database import Base, RecoveryJourneyModel, PendingSettlementModel, ActionExecutionModel
from backend.app.schemas.recovery import DecisionRequest
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.journey_service import JourneyService
from backend.app.services.recovery_orchestrator import RecoveryOrchestrator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.providers.llm_provider import MockDiagnosisProvider


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def orchestrator():
    provider = MockDiagnosisProvider()
    diag_engine = DiagnosisEngine(provider=provider)
    dec_engine = DecisionEngine(diagnosis_engine=diag_engine)
    recon = ReconciliationService()
    return RecoveryOrchestrator(
        decision_engine=dec_engine,
    )


def test_settlement_before_failure_webhook(db_session):
    tx_id = f"tx_ooo_1_{uuid.uuid4().hex[:8]}"
    recon = ReconciliationService()
    
    # 1. Settlement arrives FIRST
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "notes": {"transaction_id": tx_id},
                    "amount": 50000,
                }
            }
        }
    }
    res = recon.reconcile_settlement(db_session, "payment_link.paid", payload, webhook_event_id="evt_settle_1")
    
    assert res.status == "pending_settlement"
    assert res.journey is None
    
    # Verify it is in DB
    pending = db_session.query(PendingSettlementModel).filter_by(transaction_id=tx_id).first()
    assert pending is not None
    assert pending.amount_inr == 500.0
    assert pending.status == "PENDING"
    
    # 2. Failure webhook arrives SECOND (Journey creation)
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id=tx_id,
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds"
    )
    
    # Journey should immediately claim the pending settlement
    assert journey.status == "RECOVERED"
    assert journey.termination_reason == "EARLY_SETTLEMENT"
    assert journey.recovered_amount == 500.0
    
    # Verify pending model updated
    db_session.refresh(pending)
    assert pending.status == "CLAIMED"
    assert pending.claimed_by_journey_id == journey.journey_id
    assert pending.claimed_at is not None


def test_settlement_after_failure_webhook(db_session):
    tx_id = f"tx_ooo_2_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    recon = ReconciliationService()
    
    # 1. Failure webhook FIRST
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id=tx_id,
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds"
    )
    assert journey.status == "IN_PROGRESS"
    
    # 2. Settlement SECOND
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "notes": {"transaction_id": tx_id},
                    "amount": 50000,
                }
            }
        }
    }
    res = recon.reconcile_settlement(db_session, "payment_link.paid", payload, webhook_event_id="evt_settle_2")
    
    assert res.status == "reconciled"
    assert res.journey.status == "RECOVERED"
    
    # Verify pending table is empty for this tx
    pending = db_session.query(PendingSettlementModel).filter_by(transaction_id=tx_id).first()
    assert pending is None


def test_duplicate_early_settlement(db_session):
    tx_id = f"tx_ooo_3_{uuid.uuid4().hex[:8]}"
    recon = ReconciliationService()
    
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "notes": {"transaction_id": tx_id},
                    "amount": 50000,
                }
            }
        }
    }
    
    res1 = recon.reconcile_settlement(db_session, "payment_link.paid", payload, webhook_event_id="evt_settle_3a")
    assert res1.status == "pending_settlement"
    
    # Send duplicate payload but different webhook_event_id
    res2 = recon.reconcile_settlement(db_session, "payment_link.paid", payload, webhook_event_id="evt_settle_3b")
    assert res2.status == "pending_settlement"
    
    pendings = db_session.query(PendingSettlementModel).filter_by(transaction_id=tx_id).all()
    assert len(pendings) == 2
    
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id=tx_id,
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds"
    )
    
    assert journey.status == "RECOVERED"
    
    pendings = db_session.query(PendingSettlementModel).filter_by(transaction_id=tx_id).all()
    # At least the first one should be claimed
    assert pendings[0].status == "CLAIMED"


def test_concurrent_early_settlements(db_session):
    tx_id = f"tx_ooo_4_{uuid.uuid4().hex[:8]}"
    recon = ReconciliationService()
    
    # 2 different settlement events for the same TX
    payload1 = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"notes": {"transaction_id": tx_id}, "amount": 50000}}}}
    payload2 = {"event": "payment.captured", "payload": {"payment": {"entity": {"notes": {"transaction_id": tx_id}, "amount": 50000}}}}
    
    recon.reconcile_settlement(db_session, "payment_link.paid", payload1, webhook_event_id="evt_a")
    recon.reconcile_settlement(db_session, "payment.captured", payload2, webhook_event_id="evt_b")
    
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db=db_session,
        transaction_id=tx_id,
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds"
    )
    
    assert journey.status == "RECOVERED"
    assert journey.recovered_amount == 500.0


def test_concurrent_failure_plus_settlement_orchestrator(db_session, orchestrator):
    tx_id = f"tx_ooo_5_{uuid.uuid4().hex[:8]}"
    
    # 1. Settlement
    payload = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"notes": {"transaction_id": tx_id}, "amount": 50000}}}}
    orchestrator.reconciliation_service = ReconciliationService()
    orchestrator.reconciliation_service.reconcile_settlement(db_session, "payment_link.paid", payload, webhook_event_id="evt_c")
    
    # 2. Failure Orchestration
    req = DecisionRequest(
        transaction_id=tx_id,
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds",
        customer_id="cust_1",
        subscription_id="sub_1"
    )
    
    resp = orchestrator.process_recovery(db_session, req)
    
    assert resp.status == "RECOVERED"
    # Action selected might be 'none_early_settlement' or similar if orchestrator handles it.
    
    # Ensure no ActionExecutionModel was created (dunning prevented)
    executions = db_session.query(ActionExecutionModel).filter_by(transaction_id=tx_id).count()
    assert executions == 0
