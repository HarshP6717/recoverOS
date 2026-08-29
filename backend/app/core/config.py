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
VERSION: str = "0.3.0-phase3"
API_V1_PREFIX: str = "/v1"

# Database Configuration (SQLite for local control plane)
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_ROOT / 'recoveros_ledger.db'}")

# Machine Learning Model Artifact & AI Provider Configuration
MODEL_ARTIFACT_PATH: Path = Path(
    os.getenv(
        "MODEL_ARTIFACT_PATH",
        str(PROJECT_ROOT / "ml" / "models" / "recovery_model.joblib"),
    )
)
MODEL_VERSION: str = "recovery_logreg_v1"
AI_PROVIDER: str = os.getenv("AI_PROVIDER", "mock").lower()
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Razorpay Webhook & Test-Mode Client Configuration
RAZORPAY_TEST_MODE: bool = os.getenv("RAZORPAY_TEST_MODE", "true").lower() in ("true", "1", "yes")
RAZORPAY_LIVE_EXECUTION: bool = os.getenv("RAZORPAY_LIVE_EXECUTION", "false").lower() in ("true", "1", "yes")
RAZORPAY_API_BASE_URL: str = os.getenv("RAZORPAY_API_BASE_URL", "https://api.razorpay.com/v1")
RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "changeme_key_secret")
RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "changeme_webhook_secret")

# Test Simulation Failure Mode Switches
RAZORPAY_SIMULATE_TIMEOUT: bool = os.getenv("RAZORPAY_SIMULATE_TIMEOUT", "false").lower() in ("true", "1", "yes")
RAZORPAY_SIMULATE_GATEWAY_DOWN: bool = os.getenv("RAZORPAY_SIMULATE_GATEWAY_DOWN", "false").lower() in ("true", "1", "yes")

# CORS — explicit allowed origins for browser-facing endpoints
# Comma-separated list of origins, e.g. "http://localhost:3000,https://dashboard.example.com"
# The Razorpay webhook endpoint is server-to-server; CORS is NOT a security control for it.
ALLOWED_ORIGINS_RAW: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000,http://localhost:5173,http://127.0.0.1:5173")
ALLOWED_ORIGINS: list = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]

# Webhook replay protection: reject webhooks whose created_at is older than this many seconds.
# Set to 0 to disable. Default is 300 seconds (5 minutes).
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS: int = int(
    os.getenv("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "300")
)

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

# Customer Friction Costs in INR (₹)
# Represents the invisible cost/risk of annoying the customer or causing churn.
ACTION_FRICTION_COSTS: Dict[str, float] = {
    "retry_now": 2.00,               # Silent backend retry, very low friction
    "retry_later": 2.00,
    "send_reminder": 10.00,          # Spamming the customer
    "payment_method_update": 25.00,  # High friction, requires customer to enter new card
    "recovery_link": 5.00,           # Low friction, direct 1-click payment
    "escalate_human": 5.00,          # Humans are polite, low direct friction but high cost
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
