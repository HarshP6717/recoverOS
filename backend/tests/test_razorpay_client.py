"""
Tests for Razorpay Client Abstraction (Test-Mode / Sandbox).
"""

import pytest
from backend.app.services.razorpay_client import (
    RazorpayAuthenticationError,
    RazorpayGatewayUnavailableError,
    RazorpayTestClient,
    RazorpayTimeoutError,
)


def test_razorpay_client_valid_credentials():
    """Verify test client with valid credentials initializes and creates payment links."""
    client = RazorpayTestClient(
        key_id="rzp_test_valid123",
        key_secret="secret_abc",
        test_mode=True,
    )
    client.validate_credentials()

    link = client.create_payment_link(
        amount=1499.0,
        customer_id="cust_001",
        description="Subscription Recovery Test",
        reference_id="ref_tx_999",
    )
    assert link["id"].startswith("plink_test_")
    assert link["amount"] == 149900  # in paise
    assert link["currency"] == "INR"
    assert link["status"] == "created"
    assert "https://rzp.io/i/" in link["short_url"]


def test_razorpay_client_invalid_credentials_rejected():
    """Verify invalid key ID format raises RazorpayAuthenticationError."""
    client = RazorpayTestClient(key_id="invalid_key_prefix", key_secret="secret")
    with pytest.raises(RazorpayAuthenticationError, match="Invalid Razorpay Key ID format"):
        client.validate_credentials()

    empty_client = RazorpayTestClient(key_id="", key_secret="")
    with pytest.raises(RazorpayAuthenticationError, match="credentials missing"):
        empty_client.validate_credentials()


def test_razorpay_client_simulate_timeout():
    """Verify simulate_timeout=True raises RazorpayTimeoutError."""
    client = RazorpayTestClient(
        key_id="rzp_test_timeout",
        key_secret="secret",
        simulate_timeout=True,
    )
    with pytest.raises(RazorpayTimeoutError, match="request timed out"):
        client.create_payment_link(
            amount=999.0,
            customer_id="cust_002",
            description="Timeout test",
            reference_id="ref_timeout",
        )


def test_razorpay_client_simulate_gateway_unavailable():
    """Verify simulate_gateway_down=True raises RazorpayGatewayUnavailableError."""
    client = RazorpayTestClient(
        key_id="rzp_test_down",
        key_secret="secret",
        simulate_gateway_down=True,
    )
    with pytest.raises(RazorpayGatewayUnavailableError, match="gateway is unavailable"):
        client.fetch_payment("pay_test_123")


def test_razorpay_client_create_customer_update_session():
    """Verify update session creation returns active update URL."""
    client = RazorpayTestClient(key_id="rzp_test_update", key_secret="secret")
    session = client.create_customer_update_session(
        customer_id="cust_update_01",
        subscription_id="sub_update_01",
    )
    assert session["session_id"].startswith("sess_update_")
    assert session["status"] == "active"
    # P2-2: URL changed from non-existent recoveros.app to localhost placeholder
    assert "http://localhost:8000/update-method/" in session["update_url"]
    assert "[SIMULATED]" in session["update_url"]
