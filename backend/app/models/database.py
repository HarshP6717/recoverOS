"""
RecoverOS SQLAlchemy Database Models and SQLite Connection Management.

Implements:
1. RecoveryEventModel: Full event audit ledger for all diagnosis and recovery decisions.
2. ProcessedWebhookModel: Tracks processed webhooks with a database UNIQUE constraint
   on webhook_event_id to guarantee atomic idempotency.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Generator
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from backend.app.core.config import DATABASE_URL

Base = declarative_base()


class RecoveryEventModel(Base):
    """SQLAlchemy model for the recovery audit event ledger."""

    __tablename__ = "recovery_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    source = Column(String(32), nullable=False, default="api_direct")
    external_event_id = Column(String(128), nullable=True, index=True)

    transaction_id = Column(String(128), nullable=False, index=True)
    customer_id = Column(String(128), nullable=False, index=True)
    subscription_id = Column(String(128), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(32), nullable=False)
    failure_type = Column(String(64), nullable=False)

    attempt_number = Column(Integer, nullable=False, default=1)
    days_overdue = Column(Integer, nullable=False, default=0)
    previous_payment_count = Column(Integer, nullable=False, default=0)
    previous_success_count = Column(Integer, nullable=False, default=0)
    previous_failure_count = Column(Integer, nullable=False, default=0)
    previous_recovery_count = Column(Integer, nullable=False, default=0)
    customer_lifetime_value = Column(Float, nullable=False, default=0.0)
    contact_count = Column(Integer, nullable=False, default=0)
    subscription_age_days = Column(Integer, nullable=False, default=0)

    selected_action = Column(String(32), nullable=False)
    decision_status = Column(String(32), nullable=False)
    decision_reason = Column(Text, nullable=False)
    model_version = Column(String(32), nullable=False)

    guardrails_triggered = Column(Text, nullable=False, default="[]")
    candidate_evaluations = Column(Text, nullable=False, default="[]")
    raw_payload = Column(Text, nullable=True)

    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def get_guardrails_list(self) -> list:
        try:
            return json.loads(self.guardrails_triggered) if self.guardrails_triggered else []
        except Exception:
            return []

    def get_candidates_list(self) -> list:
        try:
            return json.loads(self.candidate_evaluations) if self.candidate_evaluations else []
        except Exception:
            return []

    def get_raw_payload_dict(self) -> dict:
        try:
            return json.loads(self.raw_payload) if self.raw_payload else {}
        except Exception:
            return {}


class ProcessedWebhookModel(Base):
    """
    SQLAlchemy model tracking processed external webhooks.
    Database UNIQUE constraint on webhook_event_id enforces atomic idempotency.
    """

    __tablename__ = "processed_webhooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    webhook_event_id = Column(String(128), unique=True, nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    recovery_event_id = Column(String(64), nullable=True)
    processed_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ActionExecutionModel(Base):
    """
    SQLAlchemy model tracking executed / simulated recovery actions.
    Maintains full execution status, timestamps, and gateway/queue response payloads.
    """

    __tablename__ = "action_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(64), unique=True, nullable=False, index=True)
    event_id = Column(String(64), nullable=False, index=True)
    transaction_id = Column(String(128), nullable=False, index=True)
    selected_action = Column(String(32), nullable=False)
    execution_status = Column(String(32), nullable=False)  # SUCCESS, SIMULATED_RECOVERED, SIMULATED_PENDING, STOPPED, EXECUTION_FAILED
    execution_timestamp = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    simulated_response = Column(Text, nullable=False, default="{}")
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def get_response_dict(self) -> dict:
        try:
            return json.loads(self.simulated_response) if self.simulated_response else {}
        except Exception:
            return {}


# Engine and Session factory
def create_db_engine(db_url: str = DATABASE_URL):
    # SQLite requires check_same_thread=False for multithreaded FastAPI requests
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args, echo=False)


engine = create_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(db_engine=engine):
    """Initializes all database tables."""
    Base.metadata.create_all(bind=db_engine)


def get_db_session() -> Generator[Session, None, None]:
    """Dependency provider for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
