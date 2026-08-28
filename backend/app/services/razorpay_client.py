"""
RecoverOS Razorpay Client Abstraction (Test-Mode / Sandbox).

Provides simulated payment gateway interactions for testing, dunning links,
and account update sessions without executing real financial transactions.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.app.core.config import (
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_SIMULATE_GATEWAY_DOWN,
    RAZORPAY_SIMULATE_TIMEOUT,
    RAZORPAY_TEST_MODE,
)

logger = logging.getLogger(__name__)


class RazorpayClientError(Exception):
    """Base exception for Razorpay client operations."""
    pass


class RazorpayAuthenticationError(RazorpayClientError):
    """Raised when API credentials are missing or invalid."""
    pass


class RazorpayTimeoutError(RazorpayClientError):
    """Raised when an API call times out."""
    pass


class RazorpayGatewayUnavailableError(RazorpayClientError):
    """Raised when the Razorpay gateway is simulated as down or returning 503."""
    pass


class RazorpayTestClient:
    """
    Test-mode client abstraction for Razorpay interactions.

    Guarantees no real money moves while supporting complete test-mode
    reproducibility and simulated gateway failure scenarios.
    """

    def __init__(
        self,
        key_id: str = RAZORPAY_KEY_ID,
        key_secret: str = RAZORPAY_KEY_SECRET,
        test_mode: bool = RAZORPAY_TEST_MODE,
        simulate_timeout: bool = RAZORPAY_SIMULATE_TIMEOUT,
        simulate_gateway_down: bool = RAZORPAY_SIMULATE_GATEWAY_DOWN,
    ):
        self.key_id = key_id
        self.key_secret = key_secret
        self.test_mode = test_mode
        self.simulate_timeout = simulate_timeout
        self.simulate_gateway_down = simulate_gateway_down

    def validate_credentials(self) -> None:
        """Validates that credentials exist and follow standard Razorpay test-key format."""
        if not self.key_id or not self.key_secret:
            raise RazorpayAuthenticationError("Razorpay credentials missing: Key ID or Key Secret is empty.")
        if not (self.key_id.startswith("rzp_test_") or self.key_id.startswith("rzp_")):
            raise RazorpayAuthenticationError(f"Invalid Razorpay Key ID format: '{self.key_id}'.")

    def _check_fault_injection(self) -> None:
        """Injects simulated network or gateway failure if configured."""
        self.validate_credentials()

        if self.simulate_gateway_down:
            logger.warning("Fault Injection: Simulating Razorpay gateway down (503 Service Unavailable)")
            raise RazorpayGatewayUnavailableError("Razorpay gateway is unavailable (HTTP 503).")

        if self.simulate_timeout:
            logger.warning("Fault Injection: Simulating Razorpay request timeout (504 Gateway Timeout)")
            raise RazorpayTimeoutError("Razorpay gateway request timed out.")

    def create_payment_link(
        self,
        amount: float,
        customer_id: str,
        description: str,
        reference_id: str,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Simulates creating a Razorpay hosted payment link for failed invoice recovery.

        Parameters
        ----------
        amount : float
            Invoice amount in INR (₹).
        customer_id : str
            Customer account ID.
        description : str
            Description displayed on the payment link checkout.
        reference_id : str
            Internal transaction or event reference.
        notes : Optional[Dict[str, Any]]
            Metadata notes attached to the payment link.

        Returns
        -------
        Dict[str, Any]
            Simulated Razorpay payment link entity.
        """
        self._check_fault_injection()

        safe_hash = hex(abs(hash(f"{reference_id}_{customer_id}")))[2:10]
        link_id = f"plink_test_{safe_hash}"
        short_url = f"https://rzp.io/i/{safe_hash}"

        return {
            "id": link_id,
            "entity": "payment_link",
            "amount": int(amount * 100),  # Amount in paise
            "currency": "INR",
            "status": "created",
            "short_url": short_url,
            "description": description,
            "customer_id": customer_id,
            "reference_id": reference_id,
            "notes": notes or {},
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }

    def create_customer_update_session(
        self,
        customer_id: str,
        subscription_id: str,
        payment_method: str = "card",
    ) -> Dict[str, Any]:
        """
        Simulates creating a secure mandate / payment method update session for customer.
        """
        self._check_fault_injection()

        safe_hash = hex(abs(hash(f"{customer_id}_{subscription_id}")))[2:10]
        session_id = f"sess_update_{safe_hash}"
        update_url = f"https://recoveros.app/update-method/{safe_hash}"

        return {
            "session_id": session_id,
            "customer_id": customer_id,
            "subscription_id": subscription_id,
            "payment_method": payment_method,
            "update_url": update_url,
            "status": "active",
            "expires_in_hours": 48,
        }

    def fetch_payment(self, payment_id: str) -> Dict[str, Any]:
        """Simulates fetching payment details by ID."""
        self._check_fault_injection()

        return {
            "id": payment_id,
            "entity": "payment",
            "status": "failed",
            "currency": "INR",
            "test_mode": True,
        }
