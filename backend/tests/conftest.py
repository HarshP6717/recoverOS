import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Generator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from backend.app.core.config import RAZORPAY_WEBHOOK_SECRET
from backend.app.core.dependencies import get_db, get_decision_engine
from backend.app.main import app
from backend.app.models.database import Base
from backend.app.services.decision_engine import DecisionEngine


@pytest.fixture(scope="function")
def test_db_engine():
    """Creates an isolated in-memory SQLite database for each test function."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db_session(test_db_engine) -> Generator[Session, None, None]:
    """Provides a fresh database session for the test."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_db_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with overridden database dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_diagnosis_payload() -> dict:
    """Sample valid payment failure diagnosis payload in INR (₹)."""
    return {
        "transaction_id": "tx_test_001",
        "customer_id": "cust_test_001",
        "subscription_id": "sub_test_001",
        "amount": 999.0,
        "payment_method": "upi",
        "failure_type": "insufficient_funds",
        "attempt_number": 1,
        "days_overdue": 1,
        "previous_payment_count": 6,
        "previous_success_count": 5,
        "previous_failure_count": 1,
        "previous_recovery_count": 1,
        "customer_lifetime_value": 4995.0,
        "contact_count": 0,
        "subscription_age_days": 180,
    }


@pytest.fixture
def sample_razorpay_webhook_payload() -> dict:
    """Sample valid Razorpay payment.failed webhook payload."""
    return {
        "entity": "event",
        "account_id": "acc_test_12345",
        "event": "payment.failed",
        "event_id": "evt_rzp_test_001",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_987654321",
                    "amount": 149900,  # 149900 paise = ₹1,499.00
                    "currency": "INR",
                    "status": "failed",
                    "method": "card",
                    "customer_id": "cust_rzp_555",
                    "subscription_id": "sub_rzp_777",
                    "error_code": "BAD_REQUEST_PAYMENT_ACCOUNT_INSUFFICIENT_BALANCE",
                    "error_description": "Payment failed due to insufficient funds in customer account",
                    "error_source": "bank",
                    "error_step": "payment_authorization",
                    "error_reason": "INSUFFICIENT_FUNDS",
                    "notes": {
                        "attempt_number": 1,
                        "days_overdue": 1,
                        "previous_payment_count": 4,
                        "previous_success_count": 4,
                        "previous_failure_count": 0,
                        "previous_recovery_count": 0,
                        "customer_lifetime_value": 5996.0,
                        "contact_count": 0,
                    },
                }
            }
        },
        "created_at": 1700000000,
    }


def generate_razorpay_signature(raw_bytes: bytes, secret: str = RAZORPAY_WEBHOOK_SECRET) -> str:
    """Helper to generate valid HMAC-SHA256 signature for test requests."""
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
