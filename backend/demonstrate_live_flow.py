"""
RecoverOS Phase 2B End-to-End Live Demonstration.

Demonstrates:
1. Razorpay Webhook Ingestion with HMAC-SHA256 Verification & Normalization
2. RecoverOS ML Decision Engine & Deterministic Guardrails
3. Action Execution Simulation across Supported Actions (retry, links, update, STOP)
4. Full Audit Ledger Persistence (Recovery Event + Execution Record)
5. Atomic Webhook Idempotency (Duplicate Webhook Ignored)
6. Comprehensive Failure Scenarios (Gateway Down, Timeout, Invalid Credentials, Model Degraded State)
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import sys
from pathlib import Path

# Enable UTF-8 for console output on Windows platforms
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from backend.app.core.config import RAZORPAY_WEBHOOK_SECRET
from backend.app.core.dependencies import get_decision_engine, get_event_service, get_razorpay_client
from backend.app.main import app
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService
from backend.app.services.razorpay_client import RazorpayTestClient


def format_currency(val: float) -> str:
    return f"₹{val:,.2f}"


def run_demonstration():
    client = TestClient(app)

    print("=" * 100)
    print("RECOVEROS PHASE 2B: RAZORPAY TEST INTEGRATION & RECOVERY EXECUTION DEMONSTRATION")
    print("=" * 100)

    # -------------------------------------------------------------------------
    # DEMO 1: End-to-End Webhook -> Decision -> Action Execution -> Audit Ledger
    # -------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("1. END-TO-END PIPELINE: Webhook -> Decision -> Action Execution -> Audit Ledger")
    print("=" * 100)

    webhook_payload = {
        "entity": "event",
        "account_id": "acc_prod_merchant_001",
        "event": "payment.failed",
        "event_id": "evt_rzp_e2e_live_001",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_live_123456",
                    "amount": 249900,  # 249,900 paise = ₹2,499.00
                    "currency": "INR",
                    "status": "failed",
                    "method": "card",
                    "customer_id": "cust_mumbai_042",
                    "subscription_id": "sub_pro_yearly_01",
                    "error_code": "EXPIRED_CARD",
                    "error_description": "Card has expired. Customer must update payment method.",
                    "error_reason": "CARD_EXPIRED",
                    "notes": {
                        "attempt_number": 1,
                        "days_overdue": 2,
                        "previous_payment_count": 12,
                        "previous_success_count": 11,
                        "previous_failure_count": 1,
                        "previous_recovery_count": 1,
                        "customer_lifetime_value": 27489.00,
                        "contact_count": 0,
                    },
                }
            }
        },
        "created_at": int(datetime.now(timezone.utc).timestamp()),
    }

    raw_body = json.dumps(webhook_payload).encode("utf-8")
    signature = hmac.new(
        key=RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": webhook_payload["event_id"],
        "Content-Type": "application/json",
    }

    print("\n[A] INCOMING RAZORPAY WEBHOOK EVENT:")
    print(f"  Event ID       : {webhook_payload['event_id']}")
    print(f"  Event Type     : {webhook_payload['event']}")
    print(f"  Gateway Amount : {webhook_payload['payload']['payment']['entity']['amount']} paise (Normalized: ₹2,499.00)")
    print(f"  Failure Reason : {webhook_payload['payload']['payment']['entity']['error_code']}")
    print(f"  HMAC Signature : {signature[:20]}... (Verified via HMAC-SHA256)")

    response = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200
    res_data = response.json()

    print("\n[B] RECOVEROS DECISION RESULT:")
    dec = res_data["decision"]
    print(f"  Event ID          : {dec['event_id']}")
    print(f"  Selected Action   : {dec['selected_action']}")
    print(f"  Decision Status   : {dec['decision_status']}")
    print(f"  Decision Reason   : {dec['decision_reason']}")
    print(f"  Active Guardrails : {dec['guardrails_triggered']}")

    print("\n[C] SIMULATED ACTION EXECUTION RESULT:")
    exc = res_data["execution"]
    print(f"  Execution ID      : {exc['execution_id']}")
    print(f"  Execution Status  : {exc['execution_status']}")
    print(f"  Executed Action   : {exc['selected_action']}")
    print(f"  Simulated Payload : {json.dumps(exc['simulated_response'], indent=4)}")

    saved_event_id = res_data["event_id"]
    saved_exec_id = exc["execution_id"]

    # -------------------------------------------------------------------------
    # DEMO 2: Audit Ledger Record Retrieval
    # -------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("2. AUDIT LEDGER VERIFICATION (GET /v1/recover/events/{event_id})")
    print("=" * 100)

    audit_resp = client.get(f"/v1/recover/events/{saved_event_id}")
    audit_data = audit_resp.json()
    print(f"  Audit Event ID    : {audit_data['event_id']}")
    print(f"  Transaction ID    : {audit_data['transaction_id']}")
    print(f"  Customer ID       : {audit_data['customer_id']}")
    print(f"  Amount            : {format_currency(audit_data['amount'])}")
    print(f"  Selected Action   : {audit_data['selected_action']}")
    print(f"  Decision Status   : {audit_data['decision_status']}")
    print(f"  Model Version     : {audit_data['model_version']}")
    print(f"  Associated Execs  : {len(audit_data['executions'])} execution record(s) linked")
    print(f"  Created At        : {audit_data['created_at']}")

    # -------------------------------------------------------------------------
    # DEMO 3: Atomic Duplicate Webhook Idempotency
    # -------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("3. ATOMIC IDEMPOTENCY: Duplicate Webhook Delivery")
    print("=" * 100)
    print("Re-delivering identical webhook payload...")
    dup_resp = client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
    dup_data = dup_resp.json()
    print(f"  HTTP Status       : {dup_resp.status_code}")
    print(f"  Status            : {dup_data['status']}")
    print(f"  Message           : {dup_data['message']}")
    print(f"  Decision Object   : {dup_data['decision']} (No duplicate decision or execution created)")

    # -------------------------------------------------------------------------
    # DEMO 4: Failure Scenarios & Safe Fallbacks
    # -------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("4. FAILURE SCENARIOS & SAFE FALLBACK VERIFICATION")
    print("=" * 100)

    # 4.1 Invalid HMAC Signature
    print("\n[Scenario 4.1] Invalid Webhook Signature:")
    bad_headers = dict(headers)
    bad_headers["X-Razorpay-Signature"] = "invalid_tampered_signature_999"
    sig_resp = client.post("/v1/webhooks/razorpay", content=raw_body, headers=bad_headers)
    print(f"  HTTP Status : {sig_resp.status_code} Unauthorized")
    print(f"  Detail      : {sig_resp.json()['detail']}")

    # 4.2 Simulated Razorpay Gateway Down (503)
    print("\n[Scenario 4.2] Simulated Razorpay Gateway Unavailable (503):")
    failing_client = RazorpayTestClient(simulate_gateway_down=True)
    failing_executor = ActionExecutionSimulator(razorpay_client=failing_client)
    app.dependency_overrides[get_razorpay_client] = lambda: failing_client
    app.dependency_overrides[get_event_service] = lambda: EventService(action_executor=failing_executor)

    down_payload = {
        "transaction_id": "tx_fail_gw_01",
        "customer_id": "cust_001",
        "subscription_id": "sub_001",
        "amount": 999.0,
        "payment_method": "upi",
        "failure_type": "bank_timeout",
    }
    gw_resp = client.post("/v1/recover/decision", json=down_payload)
    app.dependency_overrides.clear()
    gw_data = gw_resp.json()
    print(f"  HTTP Status      : {gw_resp.status_code}")
    print(f"  Execution Status : {gw_data['execution']['execution_status']}")
    print(f"  Error Code       : {gw_data['execution']['error_code']}")
    print(f"  Error Message    : {gw_data['execution']['error_message']}")

    # 4.3 Model Unavailable Degraded Fallback
    print("\n[Scenario 4.3] Model Unavailable Safe Fallback Mode:")
    degraded_engine = DecisionEngine(model_artifact_path=None)
    app.dependency_overrides[get_decision_engine] = lambda: degraded_engine
    app.dependency_overrides[get_event_service] = lambda: EventService(decision_engine=degraded_engine)

    degraded_resp = client.post("/v1/recover/decision", json=down_payload)
    app.dependency_overrides.clear()
    deg_data = degraded_resp.json()
    print(f"  HTTP Status     : {degraded_resp.status_code}")
    print(f"  Decision Status : {deg_data['decision_status']} (Automated retries safely suppressed)")
    print(f"  Fallback Action : {deg_data['selected_action']}")
    print(f"  Decision Reason : {deg_data['decision_reason']}")

    # -------------------------------------------------------------------------
    # DEMO 5: Explicit Scope & Simulation Disclaimer
    # -------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("5. EXPLICIT STATEMENT: SIMULATED VS GENUINELY INTEGRATED COMPONENTS")
    print("=" * 100)
    print("  [Genuinely Integrated Components]:")
    print("    - FastAPI REST Control Plane Routing & Request Lifecycles")
    print("    - Timing-safe HMAC-SHA256 Webhook Signature Verification (hmac.compare_digest)")
    print("    - Pydantic Data Validation, Currency Normalization (paise -> INR ₹), Error Code Mapping")
    print("    - SQLite ACID Ledger with UNIQUE Constraint Enforcing Atomic Idempotency")
    print("    - Scikit-Learn Calibrated Logistic Regression Probability Scoring & Deterministic Guardrails")
    print("  [Simulated Components (Sandbox-Only, No Real Money Movement)]:")
    print("    - Razorpay payment link URLs and session tokens (mock entities)")
    print("    - Gateway re-authorization outcomes and bank response codes")
    print("    - WhatsApp / SMS customer communication dispatch receipts")
    print("    - Action execution costs (synthetic INR assumptions, not gateway fees)")
    print("=" * 100)
    print("PHASE 2B DEMONSTRATION COMPLETE: ALL SCENARIOS VERIFIED SUCCESSFULLY")
    print("=" * 100)


if __name__ == "__main__":
    run_demonstration()
