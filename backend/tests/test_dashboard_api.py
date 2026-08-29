import json
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from sqlalchemy.pool import StaticPool
from backend.app.models.database import (
    Base,
    create_db_engine,
    SessionLocal,
    RecoveryJourneyModel,
    RecoveryEventModel,
    ActionExecutionModel,
)
from backend.app.core.dependencies import get_db

# Use an in-memory database for clean dashboard testing
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    # Setup tables before each test and drop after
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal.configure(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    yield db
    db.close()


def create_journey(db, **kwargs):
    tx_id = kwargs.get("transaction_id", f"tx_{uuid.uuid4().hex[:8]}")
    j = RecoveryJourneyModel(
        journey_id=kwargs.get("journey_id", f"jrn_{uuid.uuid4().hex[:8]}"),
        transaction_id=tx_id,
        customer_id=kwargs.get("customer_id", "cust_1"),
        amount=kwargs.get("amount", 1000.0),
        payment_method="card",
        failure_type="bank_timeout",
        status=kwargs.get("status", "IN_PROGRESS"),
        recovered_amount=kwargs.get("recovered_amount", 0.0),
        cumulative_cost=kwargs.get("cumulative_cost", 0.0),
        net_value=kwargs.get("net_value", 0.0),
        created_at=kwargs.get("created_at", datetime.now(timezone.utc)),
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j

def create_event(db, journey, **kwargs):
    evt_id = kwargs.get("event_id", f"evt_{uuid.uuid4().hex[:8]}")
    e = RecoveryEventModel(
        event_id=evt_id,
        source="api_direct",
        transaction_id=journey.transaction_id,
        customer_id=journey.customer_id,
        subscription_id="sub_1",
        amount=journey.amount,
        payment_method="card",
        failure_type="bank_timeout",
        selected_action=kwargs.get("selected_action", "recovery_link"),
        decision_status=kwargs.get("decision_status", "SUCCESS"),
        decision_reason="Test reason",
        model_version="test-1.0",
        guardrails_triggered="[]",
        candidate_evaluations="[]",
        counterfactual_data=kwargs.get("counterfactual_data"),
        created_at=kwargs.get("created_at", datetime.now(timezone.utc)),
    )
    db.add(e)
    db.commit()
    return e

def create_execution(db, journey, event_id, **kwargs):
    ex_id = kwargs.get("execution_id", f"exec_{uuid.uuid4().hex[:8]}")
    ex = ActionExecutionModel(
        execution_id=ex_id,
        event_id=event_id,
        transaction_id=journey.transaction_id,
        selected_action=kwargs.get("selected_action", "recovery_link"),
        execution_status=kwargs.get("execution_status", "SUCCESS"),
        execution_timestamp=kwargs.get("created_at", datetime.now(timezone.utc)),
        simulated_response="{}",
        created_at=kwargs.get("created_at", datetime.now(timezone.utc)),
    )
    db.add(ex)
    db.commit()
    return ex

def test_1_overview_empty_database():
    r = client.get("/v1/dashboard/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["revenue_at_risk"] == 0.0
    assert data["active_journeys"] == 0
    assert data["friction_cost"] is None

def test_2_overview_multiple_journeys(db_session):
    create_journey(db_session, amount=1000, status="IN_PROGRESS")
    create_journey(db_session, amount=500, recovered_amount=500, net_value=495, cumulative_cost=5, status="RECOVERED")
    create_journey(db_session, amount=2000, status="ESCALATED")
    
    r = client.get("/v1/dashboard/overview")
    data = r.json()
    
    assert data["revenue_at_risk"] == 3500.0
    assert data["recovered_amount"] == 500.0
    assert data["recovery_cost"] == 5.0
    assert data["net_recovered_value"] == 495.0
    assert data["active_journeys"] == 1
    assert data["recovered_journeys"] == 1
    assert data["escalated_journeys"] == 1

def test_3_pagination(db_session):
    for i in range(25):
        create_journey(db_session, amount=100)
        
    r = client.get("/v1/dashboard/journeys?limit=10&offset=0")
    data = r.json()
    assert data["total"] == 25
    assert len(data["items"]) == 10
    
    r2 = client.get("/v1/dashboard/journeys?limit=10&offset=20")
    assert len(r2.json()["items"]) == 5

def test_4_status_filtering(db_session):
    create_journey(db_session, status="EXHAUSTED")
    create_journey(db_session, status="RECOVERED")
    
    r = client.get("/v1/dashboard/journeys?status=EXHAUSTED")
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "EXHAUSTED"

def test_5_journey_detail(db_session):
    j = create_journey(db_session, amount=999, status="IN_PROGRESS")
    e = create_event(db_session, j, selected_action="retry_now", decision_status="SUCCESS")
    create_execution(db_session, j, e.event_id, selected_action="retry_now", execution_status="SUCCESS")
    
    r = client.get(f"/v1/dashboard/journeys/{j.journey_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["journey_id"] == j.journey_id
    assert data["amount"] == 999.0
    assert data["latest_diagnosis_status"] == "SUCCESS"
    assert data["latest_execution_status"] == "SUCCESS"
    assert data["selected_action"] == "retry_now"

def test_6_unknown_journey_404():
    r = client.get("/v1/dashboard/journeys/invalid_id")
    assert r.status_code == 404

def test_7_timeline_ordering(db_session):
    j = create_journey(db_session)
    t1 = datetime.now(timezone.utc) - timedelta(hours=2)
    t2 = datetime.now(timezone.utc) - timedelta(hours=1)
    
    e = create_event(db_session, j, created_at=t1)
    create_execution(db_session, j, e.event_id, created_at=t2, selected_action="recovery_link")
    
    r = client.get(f"/v1/dashboard/journeys/{j.journey_id}/timeline")
    data = r.json()
    assert len(data["events"]) == 2
    # First is event, second is execution
    assert data["events"][0]["event_type"] == "recovery_decision"
    assert data["events"][1]["event_type"] == "action_execution"
    assert data["events"][1]["is_live"] is True

def test_8_counterfactual_serialization(db_session):
    j = create_journey(db_session)
    cf_data = json.dumps({
        "selected_action": "recovery_link",
        "selected_erv": 900.0,
        "selected_probability": 0.9,
        "counterfactual_action": "retry_now",
        "counterfactual_erv": 800.0,
        "counterfactual_probability": 0.8,
        "value_difference": 100.0,
        "guardrails_applied": []
    })
    create_event(db_session, j, counterfactual_data=cf_data)
    
    r = client.get(f"/v1/dashboard/journeys/{j.journey_id}")
    data = r.json()
    assert data["counterfactual"]["value_difference"] == 100.0
    assert data["counterfactual"]["counterfactual_action"] == "retry_now"

def test_9_terminal_states(db_session):
    create_journey(db_session, status="STOPPED")
    create_journey(db_session, status="EXHAUSTED")
    create_journey(db_session, status="ESCALATED")
    
    r = client.get("/v1/dashboard/overview")
    data = r.json()
    # Check all are 1
    assert data["escalated_journeys"] == 1
    assert data["exhausted_journeys"] == 1
    # Stopped isn't explicitly requested in overview, wait it is? No, overview required: active, recovered, escalated, exhausted. 
    # Just verify the ones requested are correct.

def test_10_cancellation_pending(db_session):
    r = client.get("/v1/dashboard/overview")
    data = r.json()
    assert data["cancellation_pending_count"] is None
    
    j = create_journey(db_session)
    r = client.get(f"/v1/dashboard/journeys/{j.journey_id}")
    assert r.json()["cancellation_pending"] is None

def test_11_execution_unknown(db_session):
    j = create_journey(db_session)
    e = create_event(db_session, j)
    create_execution(db_session, j, e.event_id, execution_status="EXECUTION_UNKNOWN")
    
    r = client.get("/v1/dashboard/overview")
    assert r.json()["execution_unknown_count"] == 1

def test_12_no_secret_leakage(db_session):
    j = create_journey(db_session)
    create_event(db_session, j)
    r = client.get(f"/v1/dashboard/journeys/{j.journey_id}")
    data = r.text
    # Make sure we don't accidentally serialize secrets
    assert "SECRET" not in data.upper()
    assert "KEY" not in data.upper()
    
def test_13_malformed_pagination():
    r = client.get("/v1/dashboard/journeys?limit=-5&offset=-10")
    # FastAPI will validate query params and return 422
    assert r.status_code == 422

def test_14_large_pagination_limit_rejected():
    r = client.get("/v1/dashboard/journeys?limit=10000")
    # We bounded it to 100 with `le=100` in the route
    assert r.status_code == 422

def test_15_dashboard_endpoints_cannot_mutate(db_session):
    # Verify no POST, PUT, DELETE
    r = client.post("/v1/dashboard/overview")
    assert r.status_code == 405
    r = client.delete("/v1/dashboard/journeys/jrn_123")
    assert r.status_code == 405
