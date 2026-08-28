"""
RecoverOS Razorpay Webhook Adapter.

Implements HMAC-SHA256 signature verification and normalization of Razorpay webhook
payloads into standard RecoverOS DecisionRequest models.

NOTE: This adapter provides an integration interface for failure ingestion and
simulation. It is part of a prototype control plane designed with production safety principles,
not production-grade payment infrastructure.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional, Tuple
from backend.app.core.config import (
    FAILURE_TYPES,
    PAYMENT_METHODS,
    RAZORPAY_WEBHOOK_SECRET,
)
from backend.app.schemas.recovery import DecisionRequest

logger = logging.getLogger(__name__)


class RazorpayAdapter:
    """Adapter for verifying and normalizing Razorpay webhook payloads."""

    FAILURE_EVENTS = {
        "payment.failed",
        "subscription.halted",
        "subscription.pending",
        "invoice.payment_failed",
    }

    SETTLEMENT_EVENTS = {
        "payment_link.paid",
        "payment.captured",
        "subscription.charged",
        "payment_link.cancelled",
    }

    SUPPORTED_EVENTS = FAILURE_EVENTS | SETTLEMENT_EVENTS

    def __init__(self, secret: str = RAZORPAY_WEBHOOK_SECRET):
        self.secret = secret

    def verify_signature(self, raw_body: bytes, signature_header: Optional[str]) -> bool:
        """
        Verifies the X-Razorpay-Signature header using HMAC-SHA256 over raw request body.

        Parameters
        ----------
        raw_body : bytes
            Raw bytes received in the HTTP request body.
        signature_header : Optional[str]
            Content of the X-Razorpay-Signature header.

        Returns
        -------
        bool
            True if signature is valid and authentic, False otherwise.
        """
        if not signature_header or not self.secret:
            return False

        try:
            expected_signature = hmac.new(
                key=self.secret.encode("utf-8"),
                msg=raw_body,
                digestmod=hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(expected_signature, signature_header.strip())
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False

    def is_supported_event(self, event_type: str) -> bool:
        """Checks if event_type is a recognized webhook event."""
        return event_type in self.SUPPORTED_EVENTS

    def is_failure_event(self, event_type: str) -> bool:
        """Checks if event_type is an inbound failure trigger."""
        return event_type in self.FAILURE_EVENTS

    def is_settlement_event(self, event_type: str) -> bool:
        """Checks if event_type is a closed-loop settlement trigger."""
        return event_type in self.SETTLEMENT_EVENTS

    def normalize_failure_type(self, error_code: Optional[str], error_desc: Optional[str]) -> str:
        """
        Maps Razorpay error codes and descriptions to RecoverOS internal failure types.
        """
        combined = f"{error_code or ''} {error_desc or ''}".upper()

        if "INSUFFICIENT" in combined or "BALANCE" in combined:
            return "insufficient_funds"
        if "TIMEOUT" in combined or "GATEWAY" in combined or "TIMED_OUT" in combined:
            return "bank_timeout"
        if "EXPIRED" in combined or "CARD_EXPIRED" in combined:
            return "expired_card"
        if "STOLEN" in combined or "RESTRICTED" in combined or "BLOCKED" in combined or "HARD_DECLINE" in combined:
            return "hard_decline"
        if "AUTH" in combined or "AUTHENTICATION" in combined or "DROPPED" in combined or "CANCELLED" in combined or "ABANDONED" in combined:
            return "customer_abandoned"
        if "INVALID" in combined or "VPA" in combined or "NOT_FOUND" in combined:
            return "invalid_payment_method"
        if "DO_NOT_HONOR" in combined or "DECLINED" in combined or "SOFT_DECLINE" in combined:
            return "soft_decline"
        if "REPEATED" in combined or "MAX_ATTEMPTS" in combined:
            return "repeated_failure"

        return "unknown"

    def normalize_payment_method(self, raw_method: Optional[str]) -> str:
        """Maps Razorpay payment method names to RecoverOS standard methods."""
        m = (raw_method or "card").strip().lower()
        if m in {"upi", "vpa"}:
            return "upi"
        if m in {"card", "credit_card", "debit_card"}:
            return "card"
        if m in {"netbanking", "bank"}:
            return "netbanking"
        if m in {"nach", "emandate", "mandate_nach", "direct_debit"}:
            return "mandate_nach"
        if m in {"wallet", "prepaid"}:
            return "wallet"
        return "card"

    def normalize_webhook_payload(
        self,
        payload: Dict[str, Any],
        webhook_event_id: Optional[str] = None,
    ) -> Tuple[str, DecisionRequest]:
        """
        Normalizes a Razorpay webhook payload into an internal DecisionRequest.

        Parameters
        ----------
        payload : Dict[str, Any]
            Decoded JSON payload from Razorpay.
        webhook_event_id : Optional[str]
            Event ID header or payload ID.

        Returns
        -------
        Tuple[str, DecisionRequest]
            (event_type, normalized_request)
        """
        event_type = payload.get("event", "payment.failed")
        event_id = webhook_event_id or payload.get("event_id") or payload.get("id", "evt_unknown")

        payload_entity = payload.get("payload", {})

        # Extract payment, subscription, or invoice entity
        payment_data = payload_entity.get("payment", {}).get("entity", {})
        sub_data = payload_entity.get("subscription", {}).get("entity", {})
        invoice_data = payload_entity.get("invoice", {}).get("entity", {})

        # 1. Transaction / Payment ID
        transaction_id = (
            payment_data.get("id")
            or invoice_data.get("payment_id")
            or invoice_data.get("id")
            or f"tx_rzp_{event_id[:12]}"
        )

        # 2. Customer ID
        customer_id = (
            payment_data.get("customer_id")
            or sub_data.get("customer_id")
            or invoice_data.get("customer_id")
            or "cust_rzp_default"
        )

        # 3. Subscription ID
        subscription_id = (
            sub_data.get("id")
            or payment_data.get("subscription_id")
            or invoice_data.get("subscription_id")
            or "sub_rzp_default"
        )

        # 4. Amount: Razorpay stores amount in paise (convert to INR ₹)
        raw_amount_paise = (
            payment_data.get("amount")
            or invoice_data.get("amount")
            or sub_data.get("plan_amount")
            or 99900  # Default ₹999.00 if absent
        )
        amount_inr = round(float(raw_amount_paise) / 100.0, 2)
        if amount_inr <= 0:
            amount_inr = 999.0

        # 5. Payment method
        raw_method = payment_data.get("method") or sub_data.get("payment_method") or "card"
        payment_method = self.normalize_payment_method(raw_method)

        # 6. Failure type
        error_code = payment_data.get("error_code") or payment_data.get("error_reason")
        error_desc = payment_data.get("error_description") or invoice_data.get("status")
        failure_type = self.normalize_failure_type(error_code, error_desc)

        # 7. Metadata / Notes extraction
        notes = (
            payment_data.get("notes")
            or sub_data.get("notes")
            or invoice_data.get("notes")
            or {}
        )

        attempt_number = int(
            notes.get("attempt_number")
            or sub_data.get("retry_count")
            or invoice_data.get("attempts")
            or 1
        )
        days_overdue = int(notes.get("days_overdue", 1))
        previous_payment_count = int(notes.get("previous_payment_count", 6))
        previous_success_count = int(notes.get("previous_success_count", 5))
        previous_failure_count = int(notes.get("previous_failure_count", 1))
        previous_recovery_count = int(notes.get("previous_recovery_count", 1))
        clv = float(notes.get("customer_lifetime_value", amount_inr * previous_success_count))
        contact_count = int(notes.get("contact_count", max(0, attempt_number - 1)))
        sub_age_days = int(notes.get("subscription_age_days", previous_payment_count * 30))

        decision_request = DecisionRequest(
            transaction_id=str(transaction_id),
            customer_id=str(customer_id),
            subscription_id=str(subscription_id),
            amount=amount_inr,
            payment_method=payment_method,
            failure_type=failure_type,
            attempt_number=max(1, attempt_number),
            days_overdue=max(0, days_overdue),
            previous_payment_count=max(0, previous_payment_count),
            previous_success_count=max(0, previous_success_count),
            previous_failure_count=max(0, previous_failure_count),
            previous_recovery_count=max(0, previous_recovery_count),
            customer_lifetime_value=max(0.0, clv),
            contact_count=max(0, contact_count),
            subscription_age_days=max(0, sub_age_days),
            source="razorpay_webhook",
            external_event_id=str(event_id),
        )

        return event_type, decision_request
