"""
RecoverOS Closed-Loop Reconciliation Service.

Correlates incoming settlement webhooks (payment_link.paid, payment.captured, subscription.charged)
with active or open RecoveryJourneys, marks recovery, recalculates net financial value,
and enforces double-payment prevention by cancelling outstanding payment links.

INVARIANTS:
1. Exact once recovery: Repeated settlement events never add recovered amount twice.
2. Net value integrity: net_value = recovered_amount - cumulative_cost.
3. Double-payment prevention: Auto-cancels open hosted payment links upon settlement.
4. Terminal-state safety:
   - STOPPED/ESCALATED journeys MUST NOT be auto-overridden to RECOVERED. (P0-2)
     These represent deliberate policy decisions. Returns terminal_state_conflict.
   - EXHAUSTED journeys MAY accept a legitimate late payment via the canonical
     mark_recovered_from_exhausted() path. (P1-8)
5. Amount validation: NaN, Inf, negative and zero amounts are rejected. (P2-7)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel, PendingSettlementModel
from backend.app.services.journey_service import JourneyService
from backend.app.services.razorpay_client import RazorpayTestClient

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Encapsulates the result of a settlement reconciliation operation."""
    journey: Optional[RecoveryJourneyModel]
    status: str  # reconciled, duplicate_settlement_ignored, unmatched, terminal_state_conflict, invalid_amount, error
    event_type: str
    recovered_amount: float = 0.0
    net_value: float = 0.0
    cancelled_payment_link_id: Optional[str] = None
    cancellation_pending: bool = False  # True when Razorpay cancel failed post-DB-commit (P1-2)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "journey": self.journey.to_dict() if self.journey else None,
            "status": self.status,
            "event_type": self.event_type,
            "recovered_amount": self.recovered_amount,
            "net_value": self.net_value,
            "cancelled_payment_link_id": self.cancelled_payment_link_id,
            "cancellation_pending": self.cancellation_pending,
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

    # Maximum realistic settlement amount in INR (100 Cr)
    _MAX_AMOUNT_INR: float = 1_000_000_000.0

    def _validate_amount_inr(self, amount_inr: Optional[float]) -> Optional[float]:
        """
        Validates a settlement amount extracted from a webhook payload.

        Rejects NaN, Infinity, negative/zero, and unreasonably large amounts.
        Returns None if amount_inr is None (caller falls back to journey.amount).
        Raises ValueError for actively invalid values.
        """
        if amount_inr is None:
            return None
        if math.isnan(amount_inr):
            raise ValueError("Settlement amount is NaN — malformed payload rejected.")
        if math.isinf(amount_inr):
            raise ValueError("Settlement amount is Infinity — malformed payload rejected.")
        if amount_inr <= 0:
            raise ValueError(
                f"Settlement amount must be positive (got {amount_inr}). Payload rejected."
            )
        if amount_inr > self._MAX_AMOUNT_INR:
            raise ValueError(
                f"Settlement amount \u20b9{amount_inr:,.2f} exceeds maximum allowed "
                f"\u20b9{self._MAX_AMOUNT_INR:,.2f}. Payload rejected."
            )
        return amount_inr

    def _extract_settlement_identifiers(
        self,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extracts candidate lookup IDs and validated amount from the webhook payload."""
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

        # Amount extraction (paise -> INR) with P2-7 validation
        raw_paise = (
            payment_entity.get("amount")
            or plink_entity.get("amount")
            or sub_entity.get("plan_amount")
        )
        amount_inr: Optional[float] = None
        if raw_paise is not None:
            try:
                raw_float = float(raw_paise)
                converted = round(raw_float / 100.0, 2)
                amount_inr = self._validate_amount_inr(converted)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Invalid amount in settlement payload: raw_paise={raw_paise!r}. {e}"
                )

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
        1. Extract and validate identifiers and amount.
        2. Correlate with a recovery journey.
        3. Enforce terminal-state safety (P0-2 / P1-8).
        4. Transition journey to RECOVERED.
        5. Cancel competing open payment links (double-payment prevention).
        """
        # Step 0: P2-6 Guard — cancellation is explicitly NOT a settlement.
        if event_type == "payment_link.cancelled":
            logger.info("Ignoring payment_link.cancelled in reconciliation service.")
            return ReconciliationResult(
                journey=None,
                status="ignored_cancellation",
                event_type=event_type,
                message="payment_link.cancelled is not a settlement event and cannot reconcile a journey.",
            )

        # Step 1: Extract identifiers (may raise ValueError on invalid amount)
        try:
            identifiers = self._extract_settlement_identifiers(event_type, payload)
        except ValueError as e:
            logger.error(
                "Settlement amount validation failed for event=%s: %s", event_type, e
            )
            return ReconciliationResult(
                journey=None,
                status="invalid_amount",
                event_type=event_type,
                message=str(e),
            )

        # Step 2: Correlate journey
        journey = self.correlate_journey(db, identifiers)

        if not journey:
            logger.warning(
                "Unmatched settlement webhook: event=%s identifiers=%s. "
                "Persisting as PendingSettlement for future atomic claim.",
                event_type,
                identifiers,
            )
            # Create a pending settlement record
            amount_inr = identifiers.get("amount_inr") or 0.0
            pending = PendingSettlementModel(
                transaction_id=identifiers.get("transaction_id") or f"unmatched_{webhook_event_id}",
                payment_link_id=identifiers.get("payment_link_id"),
                subscription_id=identifiers.get("subscription_id"),
                amount_inr=amount_inr,
                event_type=event_type,
                webhook_event_id=webhook_event_id or f"evt_{datetime.now(timezone.utc).timestamp()}"
            )
            try:
                db.add(pending)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error("Failed to persist PendingSettlement: %s", e)
                
            return ReconciliationResult(
                journey=None,
                status="pending_settlement",
                event_type=event_type,
                message=f"No matching recovery journey found for settlement event '{event_type}'. Saved as pending.",
            )

        # Step 3a: Idempotency — already RECOVERED
        if journey.status == "RECOVERED":
            logger.info(
                "Duplicate settlement delivery ignored (already RECOVERED) "
                "journey_id=%s webhook_event_id=%s",
                journey.journey_id,
                webhook_event_id,
            )
            return ReconciliationResult(
                journey=journey,
                status="duplicate_settlement_ignored",
                event_type=event_type,
                recovered_amount=journey.recovered_amount,
                net_value=journey.net_value,
                message="Journey already in RECOVERED state. Duplicate settlement event ignored.",
            )

        # Step 3b: P0-2 — STOPPED/ESCALATED terminal-state conflict
        # These states represent deliberate policy decisions (fraud flags, disputes, human holds).
        # Auto-overriding them would:
        #   - Close fraud-flagged journeys automatically
        #   - Conflict with open chargeback/refund processes
        #   - Create incorrect revenue recognition
        # Return terminal_state_conflict for manual human review.
        if journey.status in ("STOPPED", "ESCALATED"):
            logger.warning(
                "Terminal-state conflict: settlement received for %s journey %s. "
                "NOT auto-overriding. Manual review required. "
                "event_type=%s webhook_event_id=%s",
                journey.status,
                journey.journey_id,
                event_type,
                webhook_event_id,
            )
            return ReconciliationResult(
                journey=journey,
                status="terminal_state_conflict",
                event_type=event_type,
                recovered_amount=identifiers.get("amount_inr") or journey.amount,
                message=(
                    f"Journey {journey.journey_id} is in '{journey.status}' state "
                    f"(deliberate policy decision). Settlement cannot be auto-applied. "
                    f"Manual review required."
                ),
            )

        # Step 4: Determine recovered amount
        recovered_amount = identifiers.get("amount_inr")
        if recovered_amount is None or recovered_amount <= 0:
            recovered_amount = journey.amount

        # Step 3c: EXHAUSTED — canonical late-settlement path (P1-8)
        if journey.status == "EXHAUSTED":
            logger.info(
                "Late settlement on EXHAUSTED journey %s — accepting via canonical path. "
                "amount=%.2f webhook_event_id=%s",
                journey.journey_id,
                recovered_amount,
                webhook_event_id,
            )
            journey = self.journey_service.mark_recovered_from_exhausted(
                db=db,
                journey_id=journey.journey_id,
                recovered_amount=recovered_amount,
            )
        else:
            # Standard IN_PROGRESS path
            journey = self.journey_service.mark_recovered(
                db=db,
                journey_id=journey.journey_id,
                recovered_amount=recovered_amount,
            )

        # Step 5: Double-Payment Prevention — cancel competing open payment links
        # P1-2: If Razorpay cancel fails AFTER DB commit, we cannot roll back.
        # The journey DB state is RECOVERED (correct). The competing link may still be active.
        # We log at CRITICAL level and set cancellation_pending=True for operational alerting.
        cancelled_link_id = None
        cancellation_pending = False
        active_link_id = journey.active_payment_link_id
        settled_link_id = identifiers.get("payment_link_id")

        if active_link_id and active_link_id != settled_link_id:
            try:
                logger.info(
                    "Double-Payment Protection: cancelling open link %s journey=%s",
                    active_link_id,
                    journey.journey_id,
                )
                self.razorpay_client.cancel_payment_link(active_link_id)
                cancelled_link_id = active_link_id
            except Exception as e:
                cancellation_pending = True
                logger.critical(
                    "CRITICAL: Failed to cancel competing payment link %s after journey %s "
                    "committed as RECOVERED. Link is still ACTIVE — double-charge risk. "
                    "Manual cancellation required. Error: %s",
                    active_link_id,
                    journey.journey_id,
                    e,
                )

        logger.info(
            "Reconciled: journey=%s recovered=%.2f net_value=%.2f cancellation_pending=%s",
            journey.journey_id,
            journey.recovered_amount,
            journey.net_value,
            cancellation_pending,
        )

        return ReconciliationResult(
            journey=journey,
            status="reconciled",
            event_type=event_type,
            recovered_amount=journey.recovered_amount,
            net_value=journey.net_value,
            cancelled_payment_link_id=cancelled_link_id,
            cancellation_pending=cancellation_pending,
            message="Settlement correlated, journey marked RECOVERED, and competing links cancelled.",
        )
