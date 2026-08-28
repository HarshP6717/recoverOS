"""
RecoverOS Configuration Module.

Defines environment variables, system constants, model artifact paths,
and synthetic action cost assumptions in INR (₹).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

# Base paths
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

# Application metadata
PROJECT_NAME: str = "RecoverOS Control Plane"
VERSION: str = "0.2.0-phase2a"
API_V1_PREFIX: str = "/v1"

# Database Configuration (SQLite for local control plane)
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_ROOT / 'recoveros_ledger.db'}")

# Machine Learning Model Artifact
MODEL_ARTIFACT_PATH: Path = Path(
    os.getenv(
        "MODEL_ARTIFACT_PATH",
        str(PROJECT_ROOT / "ml" / "models" / "recovery_model.joblib"),
    )
)
MODEL_VERSION: str = "recovery_logreg_v1"

# Razorpay Webhook & Test-Mode Client Configuration
RAZORPAY_TEST_MODE: bool = os.getenv("RAZORPAY_TEST_MODE", "true").lower() in ("true", "1", "yes")
RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "rzp_test_recoveros123")
RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "rzp_test_secret_abc456")
RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "rzp_secret_test_key_12345")

# Test Simulation Failure Mode Switches
RAZORPAY_SIMULATE_TIMEOUT: bool = os.getenv("RAZORPAY_SIMULATE_TIMEOUT", "false").lower() in ("true", "1", "yes")
RAZORPAY_SIMULATE_GATEWAY_DOWN: bool = os.getenv("RAZORPAY_SIMULATE_GATEWAY_DOWN", "false").lower() in ("true", "1", "yes")

# Domain Actions & Failure Definitions
ACTIONS: List[str] = [
    "retry_now",
    "retry_later",
    "send_reminder",
    "payment_method_update",
    "recovery_link",
    "escalate_human",
    "stop",
]

# Synthetic Action Costs in INR (₹)
# NOTE: These are synthetic simulation assumptions for controlled evaluation, not Razorpay fees.
ACTION_COSTS: Dict[str, float] = {
    "retry_now": 1.00,
    "retry_later": 1.00,
    "send_reminder": 0.50,
    "payment_method_update": 2.00,
    "recovery_link": 1.50,
    "escalate_human": 30.00,
    "stop": 0.00,
}

FAILURE_TYPES: List[str] = [
    "insufficient_funds",
    "bank_timeout",
    "soft_decline",
    "expired_card",
    "hard_decline",
    "invalid_payment_method",
    "customer_abandoned",
    "repeated_failure",
    "unknown",
]

PAYMENT_METHODS: List[str] = [
    "upi",
    "card",
    "netbanking",
    "mandate_nach",
    "wallet",
]

# Deterministic Guardrail Business Thresholds in INR (₹)
MICRO_AMOUNT_THRESHOLD_INR: float = 100.0
HIGH_VALUE_THRESHOLD_INR: float = 2000.0

# Phase-2 Fatigue & Business-Risk Thresholds
MAX_CONTACT_FATIGUE_CAP: int = 5
MAX_ATTEMPT_FATIGUE_CAP: int = 5
