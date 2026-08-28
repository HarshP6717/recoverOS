"""
RecoverOS Recovery Journey Orchestrator.

Connects incoming payment failure decision requests with stateful RecoveryJourney
lifecycle management, DecisionEngine ERV evaluation, deterministic Guardrails,
ActionExecutionSimulator, and audit ledger persistence.

STATE MACHINE:
    IN_PROGRESS -> RECOVERED
    IN_PROGRESS -> STOPPED
    IN_PROGRESS -> ESCALATED
    IN_PROGRESS -> EXHAUSTED

Terminal states are strictly immutable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel
from backend.app.schemas.recovery import DecisionRequest, DecisionResponse, ExecutionResponse
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService
from backend.app.services.journey_service import JourneyService

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Encapsulates the complete result of an orchestrated recovery execution step."""
    journey: RecoveryJourneyModel
    decision: Optional[DecisionResponse] = None
    execution: Optional[ExecutionResponse] = None
    status: str = "IN_PROGRESS"
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "journey": self.journey.to_dict() if self.journey else None,
            "decision": self.decision.model_dump() if self.decision else None,
            "execution": self.execution.model_dump() if self.execution else None,
            "status": self.status,
            "message": self.message,
        }


class RecoveryOrchestrator:
    """
    Central orchestrator coordinating RecoveryJourney state, AI decision scoring,
    guardrails enforcement, action execution, and audit trail logging.
    """

    def __init__(
        self,
        journey_service: Optional[JourneyService] = None,
        event_service: Optional[EventService] = None,
        decision_engine: Optional[DecisionEngine] = None,
        action_executor: Optional[ActionExecutionSimulator] = None,
    ):
        self.journey_service = journey_service or JourneyService()
        self.decision_engine = decision_engine or DecisionEngine()
        self.action_executor = action_executor or ActionExecutionSimulator()
        self.event_service = event_service or EventService(
            decision_engine=self.decision_engine,
            action_executor=self.action_executor,
        )

    def process_recovery(
        self,
        db: Session,
        request: DecisionRequest,
        raw_payload: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
    ) -> OrchestrationResult:
        """
        Orchestrates full recovery decision and action execution lifecycle for a payment failure:
        1. Get or create stateful RecoveryJourney.
        2. Verify journey is not already in a terminal state (RECOVERED, STOPPED, ESCALATED, EXHAUSTED).
        3. Synchronize request context with current journey state (attempt_number, round, overdue, contact).
        4. Execute DecisionEngine ERV scoring & deterministic guardrail enforcement.
        5. Persist recovery decision event to audit ledger.
        6. Execute selected action via ActionExecutor.
        7. Record action and costs in JourneyService.
        8. Apply state transitions (RECOVERED, STOPPED, ESCALATED).
        9. Return consolidated OrchestrationResult.
        """
        # 1. Get or create recovery journey
        journey = self.journey_service.get_or_create_journey(
            db=db,
            transaction_id=request.transaction_id,
            amount=request.amount,
            payment_method=request.payment_method,
            failure_type=request.failure_type,
            customer_id=request.customer_id,
            subscription_id=request.subscription_id,
            days_overdue=request.days_overdue,
            contact_count=request.contact_count,
        )

        # 2. Terminal State Check: If already terminal, do not execute further actions
        if journey.is_terminal:
            logger.info(f"Journey {journey.journey_id} is in terminal state '{journey.status}'. Skipping execution.")
            return OrchestrationResult(
                journey=journey,
                decision=None,
                execution=None,
                status=journey.status,
                message=f"Journey is already in terminal state '{journey.status}'. No further recovery actions executed.",
            )

        # 3. Synchronize request attributes with authoritative journey state
        # Ensures DecisionEngine evaluates using up-to-date sequential state
        synchronized_request = DecisionRequest(
            transaction_id=journey.transaction_id,
            customer_id=journey.customer_id or request.customer_id,
            subscription_id=journey.subscription_id or request.subscription_id,
            amount=journey.amount,
            payment_method=journey.payment_method,
            failure_type=journey.failure_type,
            attempt_number=max(journey.current_round, request.attempt_number),
            days_overdue=max(0, int(journey.days_overdue)),
            previous_payment_count=request.previous_payment_count,
            previous_success_count=request.previous_success_count,
            previous_failure_count=request.previous_failure_count,
            previous_recovery_count=request.previous_recovery_count,
            customer_lifetime_value=request.customer_lifetime_value,
            contact_count=journey.contact_count,
            subscription_age_days=request.subscription_age_days,
            source=request.source,
            external_event_id=request.external_event_id,
        )

        # 4 & 5. Evaluate Decision & Persist Audit Event Ledger
        decision = self.event_service.process_decision(
            db=db,
            request=synchronized_request,
            raw_payload=raw_payload,
            auto_execute=False,  # Orchestrator handles execution explicitly below
        )

        exec_response: Optional[ExecutionResponse] = None

        # 6. Execute selected action
        if auto_execute:
            try:
                exec_response = self.action_executor.execute_action(decision)
                self.event_service.record_execution(db, exec_response)
                decision.execution = exec_response
            except Exception as e:
                logger.error(f"Action execution failure for event {decision.event_id}: {e}")
                exec_response = ExecutionResponse(
                    execution_id=f"exec_err_{decision.event_id[:8]}",
                    event_id=decision.event_id,
                    transaction_id=decision.transaction_id,
                    selected_action=decision.selected_action,
                    execution_status="EXECUTION_FAILED",
                    execution_timestamp=datetime.now(timezone.utc),
                    simulated_response={},
                    error_code="EXECUTION_ERROR",
                    error_message=str(e),
                )
                self.event_service.record_execution(db, exec_response)

        # 7. Record action in JourneyService
        selected_action = decision.selected_action
        plink_id = None
        plink_url = None

        if exec_response and isinstance(exec_response.simulated_response, dict):
            sim_resp = exec_response.simulated_response
            plink_id = sim_resp.get("id") or sim_resp.get("payment_link_id")
            plink_url = sim_resp.get("short_url") or sim_resp.get("update_url")

        # Record action updates active_action, cumulative_cost, contact_count, net_value
        journey = self.journey_service.record_action(
            db=db,
            journey_id=journey.journey_id,
            action=selected_action,
            payment_link_id=plink_id,
            payment_link_url=plink_url,
        )

        # 8. Apply terminal state transitions based on action outcome
        if selected_action == "stop":
            journey = self.journey_service.mark_stopped(db, journey.journey_id)
        elif selected_action == "escalate_human":
            journey = self.journey_service.mark_escalated(db, journey.journey_id)
        elif exec_response and exec_response.execution_status == "SIMULATED_RECOVERED":
            journey = self.journey_service.mark_recovered(db, journey.journey_id, recovered_amount=journey.amount)

        return OrchestrationResult(
            journey=journey,
            decision=decision,
            execution=exec_response,
            status=journey.status,
            message="Recovery step orchestrated and audited successfully.",
        )

    def advance_journey_round(self, db: Session, journey_id: str) -> RecoveryJourneyModel:
        """Explicitly advances an active journey to the next round (1 -> 2, 2 -> 3, 3 -> EXHAUSTED)."""
        return self.journey_service.transition_round(db, journey_id)

    def reconcile_settlement(
        self,
        db: Session,
        transaction_id: str,
        recovered_amount: Optional[float] = None,
    ) -> Optional[RecoveryJourneyModel]:
        """
        Closed-loop settlement reconciler: marks journey as RECOVERED upon payment capture/link payment.
        """
        journey = self.journey_service.get_journey_by_transaction_id(db, transaction_id)
        if not journey:
            logger.warning(f"No journey found for transaction {transaction_id} during settlement reconciliation.")
            return None

        return self.journey_service.mark_recovered(db, journey.journey_id, recovered_amount=recovered_amount)
