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
    UniqueConstraint,
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
    counterfactual_data = Column(Text, nullable=True)
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

    def get_counterfactual_dict(self) -> Optional[dict]:
        try:
            return json.loads(self.counterfactual_data) if self.counterfactual_data else None
        except Exception:
            return None

    def get_raw_payload_dict(self) -> dict:
        try:
            return json.loads(self.raw_payload) if self.raw_payload else {}
        except Exception:
            return {}


class ProcessedWebhookModel(Base):
    """
    SQLAlchemy model tracking processed external webhooks.
    Database UNIQUE constraint on webhook_event_id enforces atomic idempotency.

    Two-phase lifecycle:
      webhook_status='RESERVED' — event accepted; business processing started.
      webhook_status='PROCESSED' — business processing completed successfully.

    If a process crashes after RESERVED but before PROCESSED, the event remains
    RESERVED and can be retried. A second delivery with a RESERVED status
    indicates an in-flight duplicate and is still safely deduplicated by the
    UNIQUE constraint.
    """

    __tablename__ = "processed_webhooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    webhook_event_id = Column(String(128), unique=True, nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    # Two-phase lifecycle: RESERVED -> PROCESSED
    webhook_status = Column(String(16), nullable=False, default="PROCESSED")
    recovery_event_id = Column(String(64), nullable=True)
    processed_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class PendingSettlementModel(Base):
    """
    SQLAlchemy model tracking out-of-order settlements.
    When a settlement webhook arrives before the failure webhook, it is stored here.
    When the RecoveryJourney is subsequently created, it claims the settlement atomically
    and prevents dunning.
    """

    __tablename__ = "pending_settlements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(128), nullable=False, index=True)
    payment_link_id = Column(String(64), nullable=True)
    subscription_id = Column(String(128), nullable=True)
    amount_inr = Column(Float, nullable=False)
    event_type = Column(String(64), nullable=False)
    webhook_event_id = Column(String(128), unique=True, nullable=False)
    
    # State tracking
    status = Column(String(16), nullable=False, default="PENDING")  # PENDING, CLAIMED
    claimed_by_journey_id = Column(String(64), nullable=True)
    
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    claimed_at = Column(DateTime, nullable=True)


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


class RecoveryJourneyModel(Base):
    """
    SQLAlchemy model tracking stateful multi-round recovery journeys.
    Maintains journey progression, active actions, payment links, and financial metrics.

    INVARIANT: transaction_id is unique across all journeys.
    Enforced at the DB level via UniqueConstraint; JourneyService handles IntegrityError
    with a read-retry (upsert pattern) to prevent duplicate journeys under concurrency.
    """

    __tablename__ = "recovery_journeys"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_journey_transaction_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    journey_id = Column(String(64), unique=True, nullable=False, index=True)
    transaction_id = Column(String(128), nullable=False, index=True)
    customer_id = Column(String(128), nullable=True, index=True)
    subscription_id = Column(String(128), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(32), nullable=False)
    failure_type = Column(String(64), nullable=False)

    current_round = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="IN_PROGRESS")  # IN_PROGRESS, RECOVERED, STOPPED, ESCALATED, EXHAUSTED
    termination_reason = Column(String(64), nullable=True)  # RECOVERED, STOP_ACTION, ESCALATE_ACTION, MAX_ROUNDS_REACHED

    active_action = Column(String(32), nullable=True)
    active_payment_link_id = Column(String(64), nullable=True)
    active_payment_link_url = Column(Text, nullable=True)

    cumulative_cost = Column(Float, nullable=False, default=0.0)
    recovered_amount = Column(Float, nullable=False, default=0.0)
    net_value = Column(Float, nullable=False, default=0.0)

    contact_count = Column(Integer, nullable=False, default=0)
    days_overdue = Column(Float, nullable=False, default=0.0)

    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in {"RECOVERED", "STOPPED", "ESCALATED", "EXHAUSTED"}

    def to_dict(self) -> dict:
        return {
            "journey_id": self.journey_id,
            "transaction_id": self.transaction_id,
            "customer_id": self.customer_id,
            "subscription_id": self.subscription_id,
            "amount": self.amount,
            "payment_method": self.payment_method,
            "failure_type": self.failure_type,
            "current_round": self.current_round,
            "status": self.status,
            "termination_reason": self.termination_reason,
            "active_action": self.active_action,
            "active_payment_link_id": self.active_payment_link_id,
            "active_payment_link_url": self.active_payment_link_url,
            "cumulative_cost": self.cumulative_cost,
            "recovered_amount": self.recovered_amount,
            "net_value": self.net_value,
            "contact_count": self.contact_count,
            "days_overdue": self.days_overdue,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


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
