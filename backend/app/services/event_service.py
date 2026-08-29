from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from backend.app.repositories.event_repository import (
    get_action_execution_by_id,
    get_action_executions_by_event_id,
    get_recovery_event_by_id,
    record_action_execution,
    record_recovery_event,
)
from backend.app.schemas.recovery import (
    ActionCandidateEvaluation,
    ActionExecutionAuditRecord,
    DecisionRequest,
    DecisionResponse,
    ExecutionResponse,
    RecoveryEventAuditRecord,
)
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine

logger = logging.getLogger(__name__)


class EventService:
    """Service for orchestrating recovery decision events, execution simulation, and audit ledger."""

    def __init__(
        self,
        decision_engine: Optional[DecisionEngine] = None,
        action_executor: Optional[ActionExecutionSimulator] = None,
    ):
        self.decision_engine = decision_engine or DecisionEngine()
        self.action_executor = action_executor or ActionExecutionSimulator()

    def process_decision(
        self,
        db: Session,
        request: DecisionRequest,
        raw_payload: Optional[dict] = None,
        auto_execute: bool = True,
    ) -> DecisionResponse:
        """
        Orchestrates full decision lifecycle:
        1. Evaluates request using decision engine.
        2. Assigns a unique event ID.
        3. Persists complete audit record to SQLite database.
        4. If auto_execute is True, simulates recovery action execution and records to ledger.
        5. Returns validated DecisionResponse.

        Raises
        ------
        Exception
            If database persistence fails, preventing un-audited action execution.
        """
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        # 1. Run evaluation pipeline
        (
            evaluations,
            selected_action,
            decision_status,
            decision_reason,
            guardrails_triggered,
            counterfactuals,
        ) = self.decision_engine.evaluate_request(request)

        # 2. Prepare audit ledger payload
        event_dict = {
            "event_id": event_id,
            "source": request.source,
            "external_event_id": request.external_event_id,
            "transaction_id": request.transaction_id,
            "customer_id": request.customer_id,
            "subscription_id": request.subscription_id,
            "amount": request.amount,
            "payment_method": request.payment_method,
            "failure_type": request.failure_type,
            "attempt_number": request.attempt_number,
            "days_overdue": request.days_overdue,
            "previous_payment_count": request.previous_payment_count,
            "previous_success_count": request.previous_success_count,
            "previous_failure_count": request.previous_failure_count,
            "previous_recovery_count": request.previous_recovery_count,
            "customer_lifetime_value": request.customer_lifetime_value,
            "contact_count": request.contact_count,
            "subscription_age_days": request.subscription_age_days,
            "selected_action": selected_action,
            "decision_status": decision_status,
            "decision_reason": decision_reason,
            "model_version": self.decision_engine.model_version,
            "guardrails_triggered": guardrails_triggered,
            "candidate_evaluations": evaluations,
            "counterfactual_data": counterfactuals.model_dump() if counterfactuals else None,
            "raw_payload": raw_payload or request.model_dump(),
            "timestamp": now,
        }

        # 3. Persist to SQLite event ledger (fails safe if DB error occurs)
        try:
            record_recovery_event(db, event_dict)
            logger.info(f"Successfully recorded recovery decision {event_id} for tx {request.transaction_id}")
        except Exception as e:
            logger.error(f"Database persistence failure for event {event_id}: {e}")
            raise RuntimeError(f"Audit ledger persistence failed: {str(e)}") from e

        # 4. Construct DecisionResponse
        decision_response = DecisionResponse(
            event_id=event_id,
            transaction_id=request.transaction_id,
            customer_id=request.customer_id,
            subscription_id=request.subscription_id,
            amount=request.amount,
            selected_action=selected_action,
            decision_status=decision_status,
            decision_reason=decision_reason,
            guardrails_triggered=guardrails_triggered,
            candidate_evaluations=evaluations,
            counterfactuals=counterfactuals,
            model_version=self.decision_engine.model_version,
            timestamp=now,
            audit_persisted=True,
            execution=None,
        )

        # 5. Optionally execute action simulation and record execution record
        if auto_execute:
            try:
                exec_response = self.action_executor.execute_action(decision_response)
                self.record_execution(db, exec_response)
                decision_response.execution = exec_response
            except Exception as e:
                logger.error(f"Action execution simulation failed for event {event_id}: {e}")

        return decision_response

    def record_execution(
        self,
        db: Session,
        execution_response: ExecutionResponse,
    ) -> None:
        """Persists an execution record to the database."""
        exec_dict = {
            "execution_id": execution_response.execution_id,
            "event_id": execution_response.event_id,
            "transaction_id": execution_response.transaction_id,
            "selected_action": execution_response.selected_action,
            "execution_status": execution_response.execution_status,
            "execution_timestamp": execution_response.execution_timestamp,
            "simulated_response": execution_response.simulated_response,
            "error_code": execution_response.error_code,
            "error_message": execution_response.error_message,
        }
        record_action_execution(db, exec_dict)

    def execute_event(self, db: Session, event_id: str) -> ExecutionResponse:
        """Triggers recovery action execution simulation for an existing event ID."""
        event_record = self.get_event(db, event_id)
        if not event_record:
            raise ValueError(f"Event ID '{event_id}' not found.")

        dummy_decision = DecisionResponse(
            event_id=event_record.event_id,
            transaction_id=event_record.transaction_id,
            customer_id=event_record.customer_id,
            subscription_id=event_record.subscription_id,
            amount=event_record.amount,
            selected_action=event_record.selected_action,
            decision_status=event_record.decision_status,
            decision_reason=event_record.decision_reason,
            guardrails_triggered=event_record.guardrails_triggered,
            candidate_evaluations=event_record.candidate_evaluations,
            counterfactuals=event_record.counterfactuals,
            model_version=event_record.model_version,
            timestamp=event_record.created_at,
            audit_persisted=True,
        )

        exec_response = self.action_executor.execute_action(dummy_decision)
        self.record_execution(db, exec_response)
        return exec_response

    def get_execution(self, db: Session, execution_id: str) -> Optional[ActionExecutionAuditRecord]:
        """Fetches an action execution audit record by execution ID."""
        exec_model = get_action_execution_by_id(db, execution_id)
        if not exec_model:
            return None

        return ActionExecutionAuditRecord(
            id=exec_model.id,
            execution_id=exec_model.execution_id,
            event_id=exec_model.event_id,
            transaction_id=exec_model.transaction_id,
            selected_action=exec_model.selected_action,
            execution_status=exec_model.execution_status,
            execution_timestamp=exec_model.execution_timestamp,
            simulated_response=exec_model.get_response_dict(),
            error_code=exec_model.error_code,
            error_message=exec_model.error_message,
            created_at=exec_model.created_at,
        )

    def get_event(self, db: Session, event_id: str) -> Optional[RecoveryEventAuditRecord]:
        """Fetches and transforms an audit record by event ID, including any execution records."""
        model_rec = get_recovery_event_by_id(db, event_id)
        if not model_rec:
            return None

        candidates_raw = model_rec.get_candidates_list()
        candidate_evals = [ActionCandidateEvaluation(**c) for c in candidates_raw]
        
        counterfactual_dict = model_rec.get_counterfactual_dict()
        counterfactual_data = None
        if counterfactual_dict:
            from backend.app.schemas.recovery import CounterfactualData
            counterfactual_data = CounterfactualData(**counterfactual_dict)

        # Fetch associated executions
        exec_models = get_action_executions_by_event_id(db, event_id)
        executions = [
            ActionExecutionAuditRecord(
                id=em.id,
                execution_id=em.execution_id,
                event_id=em.event_id,
                transaction_id=em.transaction_id,
                selected_action=em.selected_action,
                execution_status=em.execution_status,
                execution_timestamp=em.execution_timestamp,
                simulated_response=em.get_response_dict(),
                error_code=em.error_code,
                error_message=em.error_message,
                created_at=em.created_at,
            )
            for em in exec_models
        ]

        return RecoveryEventAuditRecord(
            id=model_rec.id,
            event_id=model_rec.event_id,
            source=model_rec.source,
            external_event_id=model_rec.external_event_id,
            transaction_id=model_rec.transaction_id,
            customer_id=model_rec.customer_id,
            subscription_id=model_rec.subscription_id,
            amount=model_rec.amount,
            payment_method=model_rec.payment_method,
            failure_type=model_rec.failure_type,
            attempt_number=model_rec.attempt_number,
            days_overdue=model_rec.days_overdue,
            previous_payment_count=model_rec.previous_payment_count,
            previous_success_count=model_rec.previous_success_count,
            previous_failure_count=model_rec.previous_failure_count,
            previous_recovery_count=model_rec.previous_recovery_count,
            customer_lifetime_value=model_rec.customer_lifetime_value,
            contact_count=model_rec.contact_count,
            subscription_age_days=model_rec.subscription_age_days,
            selected_action=model_rec.selected_action,
            decision_status=model_rec.decision_status,
            decision_reason=model_rec.decision_reason,
            model_version=model_rec.model_version,
            guardrails_triggered=model_rec.get_guardrails_list(),
            candidate_evaluations=candidate_evals,
            counterfactuals=counterfactual_data,
            raw_payload=model_rec.get_raw_payload_dict(),
            created_at=model_rec.created_at,
            executions=executions,
        )
