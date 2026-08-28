"""
RecoverOS Event Repository.

Provides atomic transactional access to the SQLite event ledger and
atomic webhook idempotency reservation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.database import (
    ActionExecutionModel,
    ProcessedWebhookModel,
    RecoveryEventModel,
)

logger = logging.getLogger(__name__)


def record_recovery_event(
    db: Session,
    event_data: Dict[str, Any],
) -> RecoveryEventModel:
    """
    Persists a recovery decision event to the SQLite audit ledger.

    Parameters
    ----------
    db : Session
        Active database session.
    event_data : Dict[str, Any]
        Dictionary containing all event attributes and JSON serializable fields.

    Returns
    -------
    RecoveryEventModel
        Persisted database record.
    """
    # Serialize JSON fields if needed
    guardrails_json = (
        json.dumps(event_data["guardrails_triggered"])
        if isinstance(event_data.get("guardrails_triggered"), (list, dict))
        else str(event_data.get("guardrails_triggered", "[]"))
    )

    candidates_json = (
        json.dumps(
            [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in event_data.get("candidate_evaluations", [])
            ]
        )
        if isinstance(event_data.get("candidate_evaluations"), list)
        else str(event_data.get("candidate_evaluations", "[]"))
    )

    raw_payload_json = (
        json.dumps(event_data["raw_payload"])
        if isinstance(event_data.get("raw_payload"), dict)
        else event_data.get("raw_payload")
    )

    model_record = RecoveryEventModel(
        event_id=event_data["event_id"],
        source=event_data.get("source", "api_direct"),
        external_event_id=event_data.get("external_event_id"),
        transaction_id=event_data["transaction_id"],
        customer_id=event_data["customer_id"],
        subscription_id=event_data["subscription_id"],
        amount=float(event_data["amount"]),
        payment_method=event_data["payment_method"],
        failure_type=event_data["failure_type"],
        attempt_number=int(event_data.get("attempt_number", 1)),
        days_overdue=int(event_data.get("days_overdue", 0)),
        previous_payment_count=int(event_data.get("previous_payment_count", 0)),
        previous_success_count=int(event_data.get("previous_success_count", 0)),
        previous_failure_count=int(event_data.get("previous_failure_count", 0)),
        previous_recovery_count=int(event_data.get("previous_recovery_count", 0)),
        customer_lifetime_value=float(event_data.get("customer_lifetime_value", 0.0)),
        contact_count=int(event_data.get("contact_count", 0)),
        subscription_age_days=int(event_data.get("subscription_age_days", 0)),
        selected_action=event_data["selected_action"],
        decision_status=event_data["decision_status"],
        decision_reason=event_data["decision_reason"],
        model_version=event_data["model_version"],
        guardrails_triggered=guardrails_json,
        candidate_evaluations=candidates_json,
        raw_payload=raw_payload_json,
        created_at=event_data.get("timestamp"),
    )

    try:
        db.add(model_record)
        db.commit()
        db.refresh(model_record)
        return model_record
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist recovery event {event_data.get('event_id')}: {e}")
        raise


def get_recovery_event_by_id(
    db: Session,
    event_id: str,
) -> Optional[RecoveryEventModel]:
    """Retrieves an audit record by its unique RecoverOS event ID."""
    return db.query(RecoveryEventModel).filter(RecoveryEventModel.event_id == event_id).first()


def get_recovery_events_by_transaction_id(
    db: Session,
    transaction_id: str,
) -> List[RecoveryEventModel]:
    """Retrieves all audit records for a given transaction ID."""
    return (
        db.query(RecoveryEventModel)
        .filter(RecoveryEventModel.transaction_id == transaction_id)
        .order_by(RecoveryEventModel.created_at.desc())
        .all()
    )


def reserve_webhook_event_atomic(
    db: Session,
    webhook_event_id: str,
    event_type: str,
) -> bool:
    """
    Atomically attempts to insert a webhook reservation record using the database
    UNIQUE constraint on `webhook_event_id`.

    Parameters
    ----------
    db : Session
        Active database session.
    webhook_event_id : str
        External webhook event ID (e.g. from Razorpay payload or header).
    event_type : str
        Normalized webhook event type.

    Returns
    -------
    bool
        True if the event was successfully reserved (first delivery).
        False if the event ID already exists (atomic duplicate detection).
    """
    webhook_record = ProcessedWebhookModel(
        webhook_event_id=webhook_event_id,
        event_type=event_type,
    )
    try:
        db.add(webhook_record)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        logger.info(f"Duplicate webhook delivery detected for webhook_event_id={webhook_event_id}")
        return False
    except Exception as e:
        db.rollback()
        logger.error(f"Error during webhook atomic reservation: {e}")
        raise


def attach_recovery_event_to_webhook(
    db: Session,
    webhook_event_id: str,
    recovery_event_id: str,
) -> None:
    """Updates the processed webhook record with the associated RecoverOS event ID."""
    try:
        record = (
            db.query(ProcessedWebhookModel)
            .filter(ProcessedWebhookModel.webhook_event_id == webhook_event_id)
            .first()
        )
        if record:
            record.recovery_event_id = recovery_event_id
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to attach recovery_event_id to webhook {webhook_event_id}: {e}")


def record_action_execution(
    db: Session,
    execution_data: Dict[str, Any],
) -> ActionExecutionModel:
    """
    Persists an action execution record to the SQLite ledger.
    """
    sim_resp_json = (
        json.dumps(execution_data.get("simulated_response", {}))
        if isinstance(execution_data.get("simulated_response"), dict)
        else str(execution_data.get("simulated_response", "{}"))
    )

    model_record = ActionExecutionModel(
        execution_id=execution_data["execution_id"],
        event_id=execution_data["event_id"],
        transaction_id=execution_data["transaction_id"],
        selected_action=execution_data["selected_action"],
        execution_status=execution_data["execution_status"],
        execution_timestamp=execution_data.get("execution_timestamp"),
        simulated_response=sim_resp_json,
        error_code=execution_data.get("error_code"),
        error_message=execution_data.get("error_message"),
        created_at=execution_data.get("execution_timestamp"),
    )

    try:
        db.add(model_record)
        db.commit()
        db.refresh(model_record)
        return model_record
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist action execution {execution_data.get('execution_id')}: {e}")
        raise


def get_action_execution_by_id(
    db: Session,
    execution_id: str,
) -> Optional[ActionExecutionModel]:
    """Retrieves an execution record by execution ID."""
    return db.query(ActionExecutionModel).filter(ActionExecutionModel.execution_id == execution_id).first()


def get_action_executions_by_event_id(
    db: Session,
    event_id: str,
) -> List[ActionExecutionModel]:
    """Retrieves all execution records associated with a recovery event ID."""
    return (
        db.query(ActionExecutionModel)
        .filter(ActionExecutionModel.event_id == event_id)
        .order_by(ActionExecutionModel.created_at.desc())
        .all()
    )
