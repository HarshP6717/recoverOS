"""
RecoverOS Stateful Recovery Journey Service.

Manages the lifecycle, state transitions, round progression, financial invariants,
and action auditing for recovery journeys.

STATE MACHINE:
    IN_PROGRESS -> RECOVERED
    IN_PROGRESS -> STOPPED
    IN_PROGRESS -> ESCALATED
    IN_PROGRESS -> EXHAUSTED

All terminal states are immutable.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel, PendingSettlementModel

logger = logging.getLogger(__name__)

# Customer-facing actions that increase contact fatigue counter
CUSTOMER_FACING_ACTIONS = frozenset([
    "send_reminder",
    "recovery_link",
    "payment_method_update",
    "escalate_human",
])

# Default synthetic action execution costs in INR (₹)
DEFAULT_ACTION_COSTS: Dict[str, float] = {
    "retry_now": 1.00,
    "retry_later": 1.00,
    "send_reminder": 0.50,
    "payment_method_update": 2.00,
    "recovery_link": 1.50,
    "escalate_human": 30.00,
    "stop": 0.00,
}

MAX_HORIZON_ROUNDS = 3


class JourneyService:
    """Service for managing stateful payment recovery journeys."""

    def get_or_create_journey(
        self,
        db: Session,
        transaction_id: str,
        amount: float,
        payment_method: str,
        failure_type: str,
        customer_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        days_overdue: float = 0.0,
        contact_count: int = 0,
    ) -> RecoveryJourneyModel:
        """
        Retrieves an existing active or matching journey by transaction_id, or creates a new one.

        Parameters
        ----------
        db : Session
            Active database session.
        transaction_id : str
            Transaction / payment identifier.
        amount : float
            Invoice amount in INR (₹).
        payment_method : str
            Payment method (e.g. 'upi', 'card', 'mandate_nach').
        failure_type : str
            Normalized failure diagnosis (e.g. 'insufficient_funds').
        customer_id : Optional[str]
            Customer identifier.
        subscription_id : Optional[str]
            Subscription identifier.
        days_overdue : float
            Days overdue at journey initiation.
        contact_count : int
            Initial contact count.

        Returns
        -------
        RecoveryJourneyModel
            Existing or newly created journey record.
        """
        # Validate amount
        if amount < 0:
            raise ValueError(f"Invalid journey amount: {amount}. Must be non-negative.")

        # Fast path: check for an existing journey by transaction_id
        existing = (
            db.query(RecoveryJourneyModel)
            .filter(RecoveryJourneyModel.transaction_id == transaction_id)
            .order_by(RecoveryJourneyModel.created_at.desc())
            .first()
        )
        if existing:
            return existing

        now = datetime.now(timezone.utc)
        journey_id = f"jrn_{uuid.uuid4().hex[:16]}"

        journey = RecoveryJourneyModel(
            journey_id=journey_id,
            transaction_id=str(transaction_id),
            customer_id=str(customer_id) if customer_id else None,
            subscription_id=str(subscription_id) if subscription_id else None,
            amount=float(amount),
            payment_method=str(payment_method),
            failure_type=str(failure_type),
            current_round=1,
            status="IN_PROGRESS",
            termination_reason=None,
            active_action=None,
            active_payment_link_id=None,
            active_payment_link_url=None,
            cumulative_cost=0.0,
            recovered_amount=0.0,
            net_value=0.0,
            contact_count=max(0, int(contact_count)),
            days_overdue=max(0.0, float(days_overdue)),
            created_at=now,
            updated_at=now,
        )

        try:
            db.add(journey)
            
            # Atomic check for pending settlement
            pending = (
                db.query(PendingSettlementModel)
                .filter(PendingSettlementModel.transaction_id == transaction_id, PendingSettlementModel.status == "PENDING")
                .first()
            )
            if pending:
                logger.info(
                    "Found pending out-of-order settlement for tx=%s. Claiming atomically and halting dunning.",
                    transaction_id
                )
                pending.status = "CLAIMED"
                pending.claimed_by_journey_id = journey_id
                pending.claimed_at = now
                
                journey.status = "RECOVERED"
                journey.termination_reason = "EARLY_SETTLEMENT"
                journey.recovered_amount = pending.amount_inr
                journey.net_value = journey.recovered_amount - journey.cumulative_cost
                
            db.commit()
            db.refresh(journey)
            logger.info(
                "Created new recovery journey",
                extra={"journey_id": journey_id, "transaction_id": transaction_id},
            )
            return journey
        except IntegrityError:
            # Concurrent worker created a journey for the same transaction_id first.
            # The DB UNIQUE constraint on transaction_id caught the race. Roll back and
            # re-read the record that the other worker committed.
            db.rollback()
            logger.info(
                "Concurrent journey creation detected (IntegrityError). "
                "Retrying SELECT for transaction_id=%s",
                transaction_id,
            )
            existing = (
                db.query(RecoveryJourneyModel)
                .filter(RecoveryJourneyModel.transaction_id == transaction_id)
                .order_by(RecoveryJourneyModel.created_at.desc())
                .first()
            )
            if existing:
                return existing
            # Extremely unlikely: constraint violated but row not found — re-raise.
            raise RuntimeError(
                f"IntegrityError on journey creation for tx={transaction_id} "
                "but no existing row found on retry."
            )
        except Exception as e:
            db.rollback()
            logger.error(
                "Failed to create recovery journey: %s",
                e,
                extra={"transaction_id": transaction_id},
            )
            raise

    def get_journey(self, db: Session, journey_id: str) -> Optional[RecoveryJourneyModel]:
        """Retrieves a recovery journey by its unique journey_id."""
        return db.query(RecoveryJourneyModel).filter(RecoveryJourneyModel.journey_id == journey_id).first()

    def get_journey_by_transaction_id(self, db: Session, transaction_id: str) -> Optional[RecoveryJourneyModel]:
        """Retrieves the latest recovery journey for a transaction_id."""
        return (
            db.query(RecoveryJourneyModel)
            .filter(RecoveryJourneyModel.transaction_id == transaction_id)
            .order_by(RecoveryJourneyModel.created_at.desc())
            .first()
        )

    def transition_round(self, db: Session, journey_id: str) -> RecoveryJourneyModel:
        """
        Advances the recovery journey to the next round (1 -> 2, 2 -> 3).
        If the journey is already at Round 3, transitions to EXHAUSTED (MAX_ROUNDS_REACHED).

        Raises
        ------
        ValueError
            If journey does not exist or is in a terminal state.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.is_terminal:
            raise ValueError(f"Cannot transition round: Journey {journey_id} is in terminal state '{journey.status}'.")

        now = datetime.now(timezone.utc)

        if journey.current_round == 1:
            journey.current_round = 2
            journey.days_overdue += 2.0
        elif journey.current_round == 2:
            journey.current_round = 3
            journey.days_overdue += 2.0
        elif journey.current_round >= MAX_HORIZON_ROUNDS:
            # Round 3 never becomes Round 4; marks EXHAUSTED
            journey.status = "EXHAUSTED"
            journey.termination_reason = "MAX_ROUNDS_REACHED"
        else:
            raise ValueError(f"Unexpected current_round value: {journey.current_round}")

        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            return journey
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to transition round for journey {journey_id}: {e}")
            raise

    def record_action(
        self,
        db: Session,
        journey_id: str,
        action: str,
        cost: Optional[float] = None,
        payment_link_id: Optional[str] = None,
        payment_link_url: Optional[str] = None,
    ) -> RecoveryJourneyModel:
        """
        Records an executed recovery action on the journey, adds execution cost,
        updates contact fatigue, and recalculates net value.

        Raises
        ------
        ValueError
            If journey is not found, in terminal state, or cost is negative.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.is_terminal:
            raise ValueError(f"Cannot record action: Journey {journey_id} is in terminal state '{journey.status}'.")

        if cost is None:
            cost = DEFAULT_ACTION_COSTS.get(action, 0.0)

        if cost < 0:
            raise ValueError(f"Execution cost cannot be negative: {cost}")

        now = datetime.now(timezone.utc)

        journey.active_action = str(action)
        journey.active_payment_link_id = str(payment_link_id) if payment_link_id else None
        journey.active_payment_link_url = str(payment_link_url) if payment_link_url else None
        journey.cumulative_cost += float(cost)

        # Update contact count if action is customer-facing
        if action in CUSTOMER_FACING_ACTIONS:
            journey.contact_count += 1

        # Financial invariant: net_value = recovered_amount - cumulative_cost
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            return journey
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to record action for journey {journey_id}: {e}")
            raise

    def mark_recovered(
        self,
        db: Session,
        journey_id: str,
        recovered_amount: Optional[float] = None,
    ) -> RecoveryJourneyModel:
        """
        Transitions journey to RECOVERED (idempotent for IN_PROGRESS).
        Records recovered amount and recalculates net value.

        This method only accepts IN_PROGRESS (or already-RECOVERED) journeys.
        For EXHAUSTED late settlements, use mark_recovered_from_exhausted().
        STOPPED and ESCALATED journeys must NOT be overridden here.

        Raises
        ------
        ValueError
            If journey is not found, in incompatible terminal state, or amount < 0.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        # Idempotent: if already RECOVERED, do not re-add or mutate
        if journey.status == "RECOVERED":
            return journey

        if journey.is_terminal:
            raise ValueError(f"Cannot mark journey as RECOVERED from terminal state '{journey.status}'.")

        if recovered_amount is None:
            recovered_amount = journey.amount

        if recovered_amount < 0:
            raise ValueError(f"Recovered amount cannot be negative: {recovered_amount}")

        now = datetime.now(timezone.utc)

        journey.status = "RECOVERED"
        journey.termination_reason = "RECOVERED"
        journey.recovered_amount = float(recovered_amount)
        # Financial invariant: net_value = recovered_amount - cumulative_cost
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            logger.info(
                "Journey marked RECOVERED (Net Value: ₹%.2f)",
                journey.net_value,
                extra={"journey_id": journey_id},
            )
            return journey
        except Exception as e:
            db.rollback()
            logger.error(
                "Failed to mark journey as recovered: %s",
                e,
                extra={"journey_id": journey_id},
            )
            raise

    def mark_recovered_from_exhausted(
        self,
        db: Session,
        journey_id: str,
        recovered_amount: float,
    ) -> RecoveryJourneyModel:
        """
        Accepts a legitimate late payment on an EXHAUSTED journey.

        This is the canonical recovery path for EXHAUSTED journeys (P1-8):
        - EXHAUSTED is a policy exhaustion, not a manual decision.
        - A customer paying after exhaustion is a legitimate settlement.
        - STOPPED and ESCALATED journeys are explicitly NOT accepted here;
          they require manual human review.

        Raises
        ------
        ValueError
            If journey is not EXHAUSTED or amount is invalid.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.status == "RECOVERED":
            return journey  # already recovered, idempotent

        if journey.status != "EXHAUSTED":
            raise ValueError(
                f"mark_recovered_from_exhausted only accepts EXHAUSTED journeys. "
                f"Got status '{journey.status}' for journey {journey_id}."
            )

        if recovered_amount <= 0:
            raise ValueError(f"Recovered amount must be positive: {recovered_amount}")

        now = datetime.now(timezone.utc)

        journey.status = "RECOVERED"
        journey.termination_reason = "LATE_SETTLEMENT_AFTER_EXHAUSTION"
        journey.recovered_amount = float(recovered_amount)
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            logger.info(
                "Journey (previously EXHAUSTED) marked RECOVERED via late settlement "
                "(Net Value: ₹%.2f)",
                journey.net_value,
                extra={"journey_id": journey_id},
            )
            return journey
        except Exception as e:
            db.rollback()
            logger.error(
                "Failed to mark exhausted journey as recovered: %s",
                e,
                extra={"journey_id": journey_id},
            )
            raise

    def mark_stopped(self, db: Session, journey_id: str) -> RecoveryJourneyModel:
        """
        Transitions journey to STOPPED (idempotent).

        Raises
        ------
        ValueError
            If journey is not found or in incompatible terminal state.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.status == "STOPPED":
            return journey

        if journey.is_terminal:
            raise ValueError(f"Cannot mark journey as STOPPED from terminal state '{journey.status}'.")

        now = datetime.now(timezone.utc)

        journey.status = "STOPPED"
        journey.termination_reason = "STOP_ACTION"
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            logger.info(f"Journey {journey_id} marked STOPPED")
            return journey
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark journey {journey_id} as stopped: {e}")
            raise

    def mark_escalated(self, db: Session, journey_id: str) -> RecoveryJourneyModel:
        """
        Transitions journey to ESCALATED (idempotent).

        Raises
        ------
        ValueError
            If journey is not found or in incompatible terminal state.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.status == "ESCALATED":
            return journey

        if journey.is_terminal:
            raise ValueError(f"Cannot mark journey as ESCALATED from terminal state '{journey.status}'.")

        now = datetime.now(timezone.utc)

        journey.status = "ESCALATED"
        journey.termination_reason = "ESCALATE_ACTION"
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            logger.info(f"Journey {journey_id} marked ESCALATED")
            return journey
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark journey {journey_id} as escalated: {e}")
            raise

    def mark_exhausted(self, db: Session, journey_id: str) -> RecoveryJourneyModel:
        """
        Transitions journey to EXHAUSTED (idempotent).

        Raises
        ------
        ValueError
            If journey is not found or in incompatible terminal state.
        """
        journey = self.get_journey(db, journey_id)
        if not journey:
            raise ValueError(f"Recovery journey '{journey_id}' not found.")

        if journey.status == "EXHAUSTED":
            return journey

        if journey.is_terminal:
            raise ValueError(f"Cannot mark journey as EXHAUSTED from terminal state '{journey.status}'.")

        now = datetime.now(timezone.utc)

        journey.status = "EXHAUSTED"
        journey.termination_reason = "MAX_ROUNDS_REACHED"
        journey.net_value = journey.recovered_amount - journey.cumulative_cost
        journey.updated_at = now

        try:
            db.commit()
            db.refresh(journey)
            logger.info(f"Journey {journey_id} marked EXHAUSTED")
            return journey
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark journey {journey_id} as exhausted: {e}")
            raise
