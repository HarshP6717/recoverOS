"""
RecoverOS Live Mode Verification Script.

Executes:
1. Real Gemini 1.5 Flash API diagnosis call (via GeminiDiagnosisProvider).
2. Real Razorpay Test-Mode REST API payment link creation (via RazorpayTestClient).

Usage:
    export GEMINI_API_KEY="your_gemini_key"
    export RAZORPAY_KEY_ID="rzp_test_..."
    export RAZORPAY_KEY_SECRET="your_secret"
    export RAZORPAY_LIVE_EXECUTION="true"
    python backend/scripts/verify_live_mode.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import (
    GEMINI_API_KEY,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_LIVE_EXECUTION,
)
from backend.app.schemas.diagnosis import DiagnosisRequest as IntelDiagnosisRequest
from backend.app.providers.llm_provider import GeminiDiagnosisProvider
from backend.app.services.razorpay_client import RazorpayTestClient


def verify_gemini_live():
    print("\n--- [1/2] Verifying Gemini Live API ---")
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not api_key:
        print("[SKIP] GEMINI_API_KEY not set. Provide GEMINI_API_KEY to test live Gemini calls.")
        return False

    provider = GeminiDiagnosisProvider(api_key=api_key)
    req = IntelDiagnosisRequest(
        failure_reason="expired_card",
        payment_amount=1499.0,
        payment_method="card",
        customer_history="CLV: 8994.0, Failures: 0",
        previous_attempts=0,
        days_overdue=1,
        journey_round=1,
    )
    print(f"Calling Gemini API (gemini-1.5-flash) for failure_reason='{req.failure_reason}'...")
    diag = provider.get_diagnosis(req)
    print(f"  Diagnosis Category:   {diag.failure_category}")
    print(f"  AI Confidence:        {diag.confidence:.2f}")
    print(f"  Recovery Probs:       {diag.recovery_probabilities}")
    print(f"  Reasoning:            {diag.reasoning_summary}")
    print("[SUCCESS] Gemini API responded with valid structured schema.")
    return True


def verify_razorpay_live():
    print("\n--- [2/2] Verifying Razorpay Live Test-Mode API ---")
    key_id = os.getenv("RAZORPAY_KEY_ID") or RAZORPAY_KEY_ID
    key_secret = os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET
    live_mode = os.getenv("RAZORPAY_LIVE_EXECUTION", "false").lower() in ("true", "1", "yes") or RAZORPAY_LIVE_EXECUTION

    if not live_mode:
        print("[SKIP] RAZORPAY_LIVE_EXECUTION is false. Set RAZORPAY_LIVE_EXECUTION=true and valid credentials.")
        return False
    if not key_id or not key_secret or key_id == "rzp_test_recoveros123":
        print("[SKIP] Real Razorpay test credentials not configured.")
        return False

    client = RazorpayTestClient(
        key_id=key_id,
        key_secret=key_secret,
        live_execution=True,
    )
    print(f"Calling Razorpay Test Mode API (POST https://api.razorpay.com/v1/payment_links)...")
    res = client.create_payment_link(
        amount=1499.0,
        customer_id="cust_live_verify_001",
        description="RecoverOS Live Verification Payment Link",
        reference_id="ref_live_verify_001",
        customer_name="Test Merchant",
        customer_email="merchant.test@example.com",
    )
    print(f"  Payment Link ID:      {res.get('id')}")
    print(f"  Short URL:            {res.get('short_url')}")
    print(f"  Status:               {res.get('status')}")
    print(f"  Amount (paise):       {res.get('amount')}")
    
    # Clean up by cancelling the created test link
    plink_id = res.get("id")
    if plink_id:
        print(f"Cancelling test payment link {plink_id}...")
        cancel_res = client.cancel_payment_link(plink_id)
        print(f"  Cancelled Status:     {cancel_res.get('status')}")

    print("[SUCCESS] Razorpay Test Mode API payment link created and cancelled successfully.")
    return True


if __name__ == "__main__":
    print("==================================================")
    print("RECOVEROS LIVE MODE VERIFICATION")
    print("==================================================")
    g_ok = verify_gemini_live()
    r_ok = verify_razorpay_live()
    print("==================================================")
    if g_ok and r_ok:
        print("ALL LIVE INTEGRATIONS VERIFIED SUCCESSFULLY.")
    else:
        print("Verification finished (some live services skipped if keys not provided).")
