"""
RecoverOS Razorpay Client Abstraction (Test-Mode / Sandbox & Live REST API).

Provides:
- Simulated payment gateway interactions for offline testing and evaluation
- Genuine HTTP execution against Razorpay Test Mode REST API (when RAZORPAY_LIVE_EXECUTION=true)
- Safe error handling, credential sanitization, fault injection, and response normalization.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx

from backend.app.core.config import (
    RAZORPAY_API_BASE_URL,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_LIVE_EXECUTION,
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
    Client abstraction for Razorpay interactions.

    Supports:
    1. Offline deterministic simulation (default, network-free).
    2. Genuine Test-Mode REST API calls via httpx (when live_execution=True).
    """

    def __init__(
        self,
        key_id: str = RAZORPAY_KEY_ID,
        key_secret: str = RAZORPAY_KEY_SECRET,
        test_mode: bool = RAZORPAY_TEST_MODE,
        live_execution: bool = RAZORPAY_LIVE_EXECUTION,
        base_url: str = RAZORPAY_API_BASE_URL,
        timeout: float = 10.0,
        simulate_timeout: bool = RAZORPAY_SIMULATE_TIMEOUT,
        simulate_gateway_down: bool = RAZORPAY_SIMULATE_GATEWAY_DOWN,
    ):
        self.key_id = key_id
        self.key_secret = key_secret
        self.test_mode = test_mode
        self.live_execution = live_execution
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
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
        customer_name: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_contact: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Creates a Razorpay hosted payment link for invoice recovery.

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
        customer_name : Optional[str]
            Customer display name.
        customer_email : Optional[str]
            Customer email address.
        customer_contact : Optional[str]
            Customer mobile number.

        Returns
        -------
        Dict[str, Any]
            Razorpay payment link entity.
        """
        # --- Live HTTP Execution Mode ---
        if self.live_execution:
            self.validate_credentials()
            amount_paise = int(round(amount * 100))
            payload = {
                "amount": amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "description": description or f"Subscription Recovery Payment (Ref: {reference_id})",
                "reference_id": str(reference_id),
                "customer": {
                    "name": customer_name or "RecoverOS Customer",
                    "email": customer_email or f"{customer_id}@example.com",
                    "contact": customer_contact or "+919876543210",
                },
                "notify": {
                    "sms": True,
                    "email": True,
                },
                "reminder_enable": True,
                "notes": notes or {},
            }

            url = f"{self.base_url}/payment_links"
            try:
                with httpx.Client(auth=(self.key_id, self.key_secret), timeout=self.timeout) as client:
                    resp = client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(f"Live Razorpay Payment Link created: id={data.get('id')}, url={data.get('short_url')}")
                    return data
            except httpx.TimeoutException as e:
                logger.error(f"Live Razorpay API call timed out on POST {url}")
                raise RazorpayTimeoutError(f"Razorpay API request timed out: {e}")
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                logger.error(f"Live Razorpay API returned error status {status_code} on POST {url}")
                if status_code == 401:
                    raise RazorpayAuthenticationError(f"Authentication failed with Razorpay API (HTTP 401)")
                elif status_code in (502, 503, 504):
                    raise RazorpayGatewayUnavailableError(f"Razorpay gateway unavailable (HTTP {status_code})")
                else:
                    raise RazorpayClientError(f"Razorpay API error HTTP {status_code}: {e.response.text}")
            except Exception as e:
                logger.error(f"Unexpected error communicating with Razorpay API: {e}")
                raise RazorpayClientError(f"Failed to execute live Razorpay API call: {e}")

        # --- Offline Simulation Mode ---
        self._check_fault_injection()

        safe_hash = hex(abs(hash(f"{reference_id}_{customer_id}")))[2:10]
        link_id = f"plink_test_{safe_hash}"
        short_url = f"https://rzp.io/i/{safe_hash}"

        return {
            "id": link_id,
            "entity": "payment_link",
            "amount": int(round(amount * 100)),  # Amount in paise
            "currency": "INR",
            "status": "created",
            "short_url": short_url,
            "description": description,
            "customer_id": customer_id,
            "reference_id": reference_id,
            "notes": notes or {},
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }

    def cancel_payment_link(self, payment_link_id: str) -> Dict[str, Any]:
        """
        Cancels an outstanding Razorpay payment link to prevent double billing.

        Parameters
        ----------
        payment_link_id : str
            Razorpay payment link ID (e.g. 'plink_xxx').

        Returns
        -------
        Dict[str, Any]
            Cancelled payment link entity.
        """
        # --- Live HTTP Execution Mode ---
        if self.live_execution:
            self.validate_credentials()
            url = f"{self.base_url}/payment_links/{payment_link_id}/cancel"
            try:
                with httpx.Client(auth=(self.key_id, self.key_secret), timeout=self.timeout) as client:
                    resp = client.post(url)
                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(f"Live Razorpay Payment Link cancelled: id={payment_link_id}")
                    return data
            except httpx.TimeoutException as e:
                logger.error(f"Live Razorpay API call timed out on POST {url}")
                raise RazorpayTimeoutError(f"Razorpay API request timed out: {e}")
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                logger.error(f"Live Razorpay API error {status_code} cancelling link {payment_link_id}")
                if status_code == 401:
                    raise RazorpayAuthenticationError("Authentication failed with Razorpay API (HTTP 401)")
                elif status_code in (502, 503, 504):
                    raise RazorpayGatewayUnavailableError(f"Razorpay gateway unavailable (HTTP {status_code})")
                else:
                    raise RazorpayClientError(f"Razorpay API error HTTP {status_code}: {e.response.text}")
            except Exception as e:
                logger.error(f"Unexpected error cancelling Razorpay payment link {payment_link_id}: {e}")
                raise RazorpayClientError(f"Failed to cancel live payment link: {e}")

        # --- Offline Simulation Mode ---
        self._check_fault_injection()

        return {
            "id": payment_link_id,
            "entity": "payment_link",
            "status": "cancelled",
            "cancelled_at": int(datetime.now(timezone.utc).timestamp()),
        }

    def create_customer_update_session(
        self,
        customer_id: str,
        subscription_id: str,
        payment_method: str = "card",
    ) -> Dict[str, Any]:
        """
        Creates a secure mandate / payment method update session for customer.
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
        """Fetches payment details by ID."""
        if self.live_execution:
            self.validate_credentials()
            url = f"{self.base_url}/payments/{payment_id}"
            try:
                with httpx.Client(auth=(self.key_id, self.key_secret), timeout=self.timeout) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as e:
                logger.error(f"Failed to fetch payment {payment_id} from Razorpay: {e}")
                raise RazorpayClientError(f"Failed to fetch payment {payment_id}: {e}")

        self._check_fault_injection()

        return {
            "id": payment_id,
            "entity": "payment",
            "status": "failed",
            "currency": "INR",
            "test_mode": True,
        }
