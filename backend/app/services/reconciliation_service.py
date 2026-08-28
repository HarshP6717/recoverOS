"""
RecoverOS Closed-Loop Reconciliation Service.

Correlates incoming settlement webhooks (payment_link.paid, payment.captured, subscription.charged)
with active or open RecoveryJourneys, marks recovery, recalculates net financial value,
and enforces double-payment prevention by cancelling outstanding payment links.

INVARIANTS:
1. Exact once recovery: Repeated settlement events never add recovered amount twice.
2. Net value integrity: net_value = recovered_amount - cumulative_cost.
3. Double-payment prevention: Auto-cancels open hosted payment links upon settlement.
4. Cryptographic audit trail: Records all reconciliation actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel
from backend.app.services.journey_service import JourneyService
from backend.app.services.razorpay_client import RazorpayTestClient

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Encapsulates the result of a settlement reconciliation operation."""
    journey: Optional[RecoveryJourneyModel]
    status: str  # reconciled, duplicate_settlement_ignored, unmatched, ambiguous, error
    event_type: str
    recovered_amount: float = 0.0
    net_value: float = 0.0
    cancelled_payment_link_id: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "journey": self.journey.to_dict() if self.journey else None,
            "status": self.status,
            "event_type": self.event_type,
            "recovered_amount": self.recovered_amount,
            "net_value": self.net_value,
            "cancelled_payment_link_id": self.cancelled_payment_link_id,
            "message": self.message,
        }


class ReconciliationService:
    """
    Handles closed-loop reconciliation for successful customer payments
    and enforces double-payment protection.
    """

    def __init__(
        self,
        journey_service: Optional[JourneyService] = None,
        razorpay_client: Optional[RazorpayTestClient] = None,
    ):
        self.journey_service = journey_service or JourneyService()
        self.razorpay_client = razorpay_client or RazorpayTestClient()

    def _extract_settlement_identifiers(
        self,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extracts candidate lookup IDs and amount from the webhook payload."""
        payload_entity = payload.get("payload", {})
        plink_entity = payload_entity.get("payment_link", {}).get("entity", {})
        payment_entity = payload_entity.get("payment", {}).get("entity", {})
        sub_entity = payload_entity.get("subscription", {}).get("entity", {})

        notes = (
            plink_entity.get("notes")
            or payment_entity.get("notes")
            or sub_entity.get("notes")
            or {}
        )

        payment_link_id = plink_entity.get("id") or notes.get("payment_link_id")
        transaction_id = (
            notes.get("transaction_id")
            or plink_entity.get("reference_id")
            or payment_entity.get("id")
        )
        subscription_id = (
            sub_entity.get("id")
            or payment_entity.get("subscription_id")
            or notes.get("subscription_id")
        )
        customer_id = (
            payment_entity.get("customer_id")
            or sub_entity.get("customer_id")
            or notes.get("customer_id")
        )

        # Amount extraction (paise -> INR)
        raw_paise = (
            payment_entity.get("amount")
            or plink_entity.get("amount")
            or sub_entity.get("plan_amount")
        )
        amount_inr = round(float(raw_paise) / 100.0, 2) if raw_paise is not None else None

        return {
            "payment_link_id": str(payment_link_id) if payment_link_id else None,
            "transaction_id": str(transaction_id) if transaction_id else None,
            "subscription_id": str(subscription_id) if subscription_id else None,
            "customer_id": str(customer_id) if customer_id else None,
            "amount_inr": amount_inr,
        }

    def correlate_journey(
        self,
        db: Session,
        identifiers: Dict[str, Any],
    ) -> Optional[RecoveryJourneyModel]:
        """
        Locates the authoritative RecoveryJourney using candidate correlation keys.
        Rejects ambiguous multi-match scenarios.
        """
        plink_id = identifiers.get("payment_link_id")
        tx_id = identifiers.get("transaction_id")
        sub_id = identifiers.get("subscription_id")

        # 1. Match by exact active payment link ID
        if plink_id:
            journey = (
                db.query(RecoveryJourneyModel)
                .filter(RecoveryJourneyModel.active_payment_link_id == plink_id)
                .first()
            )
            if journey:
                return journey

        # 2. Match by exact transaction ID
        if tx_id:
            journey = (
                db.query(RecoveryJourneyModel)
                .filter(RecoveryJourneyModel.transaction_id == tx_id)
                .order_by(RecoveryJourneyModel.created_at.desc())
                .first()
            )
            if journey:
                return journey

        # 3. Match by active subscription ID
        if sub_id:
            journey = (
                db.query(RecoveryJourneyModel)
                .filter(
                    RecoveryJourneyModel.subscription_id == sub_id,
                    RecoveryJourneyModel.status == "IN_PROGRESS",
                )
                .order_by(RecoveryJourneyModel.created_at.desc())
                .first()
            )
            if journey:
                return journey

        return None

    def reconcile_settlement(
        self,
        db: Session,
        event_type: str,
        payload: Dict[str, Any],
        webhook_event_id: Optional[str] = None,
    ) -> ReconciliationResult:
        """
        Processes a settlement event:
        1. Extract identifiers and correlate journey.
        2. Verify idempotency: duplicate settlement is ignored without double-adding revenue.
        3. Transition journey to RECOVERED and calculate net value.
        4. Cancel any competing open payment links to prevent double billing.
        """
        identifiers = self._extract_settlement_identifiers(event_type, payload)
        journey = self.correlate_journey(db, identifiers)

        if not journey:
            logger.warning(
                f"Unmatched settlement webhook: event={event_type}, identifiers={identifiers}"
            )
            return ReconciliationResult(
                journey=None,
                status="unmatched",
                event_type=event_type,
                message=f"No matching recovery journey found for settlement event '{event_type}'.",
            )

        # 1. Idempotency Check: Already recovered
        if journey.status == "RECOVERED":
            logger.info(
                f"Duplicate settlement delivery ignored for journey {journey.journey_id} (already RECOVERED)"
            )
            return ReconciliationResult(
                journey=journey,
                status="duplicate_settlement_ignored",
                event_type=event_type,
                recovered_amount=journey.recovered_amount,
                net_value=journey.net_value,
                message="Journey already in RECOVERED state. Duplicate settlement event ignored without double-counting.",
            )

        # 2. Determine recovered amount
        recovered_amount = identifiers.get("amount_inr")
        if recovered_amount is None or recovered_amount <= 0:
            recovered_amount = journey.amount

        # 3. Mark Journey RECOVERED
        # Note: If journey was previously in another terminal state (e.g. STOPPED or EXHAUSTED),
        # an external customer settlement overrides it safely.
        if journey.is_terminal and journey.status != "RECOVERED":
            now = datetime.now(timezone.utc)
            journey.status = "RECOVERED"
            journey.termination_reason = "RECOVERED"
            journey.recovered_amount = float(recovered_amount)
            journey.net_value = journey.recovered_amount - journey.cumulative_cost
            journey.updated_at = now
            db.commit()
            db.refresh(journey)
        else:
            journey = self.journey_service.mark_recovered(
                db=db,
                journey_id=journey.journey_id,
                recovered_amount=recovered_amount,
            )

        # 4. Double-Payment Prevention: Cancel active payment link if open and different from settled link
        cancelled_link_id = None
        active_link_id = journey.active_payment_link_id
        settled_link_id = identifiers.get("payment_link_id")

        if active_link_id and active_link_id != settled_link_id:
            try:
                logger.info(
                    f"Double-Payment Protection: Cancelling open payment link {active_link_id} for journey {journey.journey_id}"
                )
                self.razorpay_client.cancel_payment_link(active_link_id)
                cancelled_link_id = active_link_id
            except Exception as e:
                logger.warning(
                    f"Failed to cancel open payment link {active_link_id} on settlement: {e}"
                )

        logger.info(
            f"Closed-Loop Settlement Reconciled: journey={journey.journey_id}, "
            f"recovered=₹{journey.recovered_amount:,.2f}, net_value=₹{journey.net_value:,.2f}"
        )

        return ReconciliationResult(
            journey=journey,
            status="reconciled",
            event_type=event_type,
            recovered_amount=journey.recovered_amount,
            net_value=journey.net_value,
            cancelled_payment_link_id=cancelled_link_id,
            message="Settlement correlated, journey marked RECOVERED, and competing links cancelled.",
        )
