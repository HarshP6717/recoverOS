import sys
import os
import json
import uuid
import time
import hmac
import hashlib
from typing import Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from backend.app.main import app
from backend.app.core.config import (
    RAZORPAY_WEBHOOK_SECRET,
    RAZORPAY_LIVE_EXECUTION,
    AI_PROVIDER,
)
from fastapi.testclient import TestClient

client = TestClient(app)

def generate_signature(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

def send_webhook(event_name: str, payload: Dict[str, Any]) -> dict:
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    signature = generate_signature(payload_bytes, RAZORPAY_WEBHOOK_SECRET)
    
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": signature
    }
    
    print(f"\n[DEMO] Triggering Webhook: {event_name}")
    response = client.post("/v1/webhooks/razorpay", content=payload_bytes, headers=headers)
    
    if response.status_code != 200:
        print(f"[DEMO ERROR] Webhook failed: {response.text}")
        sys.exit(1)
        
    return response.json()

def run_golden_demo():
    mode_str = "LIVE RAZORPAY TEST API" if RAZORPAY_LIVE_EXECUTION else "DETERMINISTIC LOCAL SIMULATION (Safe Sandbox)"
    ai_str = "Gemini Live API" if AI_PROVIDER == "gemini" else "Mock Deterministic Provider"
    print("==================================================")
    print("RECOVEROS GOLDEN DEMO")
    print("==================================================")
    print(f"Execution Mode: {mode_str}")
    print(f"AI Provider:    {ai_str}")
    print("--------------------------------------------------")
    print("This script deterministically walks through a complete")
    print("payment recovery lifecycle, demonstrating the closed-loop")
    print("architecture from failure to reconciliation.")
    print("==================================================\n")
    
    # 1. Simulate a payment failure
    transaction_id = f"pay_{uuid.uuid4().hex[:14]}"
    customer_id = "cust_demo_789"
    
    failure_payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": transaction_id,
                    "amount": 100000, # 1000 INR
                    "currency": "INR",
                    "status": "failed",
                    "method": "card",
                    "customer_id": customer_id,
                    "email": "demo@example.com",
                    "contact": "+919876543210",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed due to expired card",
                    "error_reason": "expired_card"
                }
            }
        },
        "created_at": int(time.time())
    }
    
    print(f"[STEP 1] Generating Razorpay Payment Failure for {transaction_id}...")
    failure_result = send_webhook("payment.failed", failure_payload)
    
    if failure_result.get("status") != "processed":
        print(f"Failed to process webhook. Result: {failure_result}")
        sys.exit(1)
        
    # Retrieve Journey ID from the processed orchestrator
    # For demo purposes, we can query the dashboard API to find this transaction
    print("\n[STEP 2] Fetching Journey Details from Command Center API...")
    journeys_resp = client.get(f"/v1/dashboard/journeys?search={transaction_id}")
    items = journeys_resp.json().get("items", [])
    if not items:
        print("Journey not found in dashboard API!")
        sys.exit(1)
        
    journey_id = items[0]["journey_id"]
    print(f"         Found Journey ID: {journey_id}")
    
    detail_resp = client.get(f"/v1/dashboard/journeys/{journey_id}")
    detail = detail_resp.json()
    
    print("\n[STEP 3] Reviewing AI Diagnosis & Economic Decision...")
    print(f"         AI Diagnosis: {detail['latest_diagnosis_status']}")
    print("         Candidate Evaluations:")
    for cand in detail['candidate_evaluations']:
        tag = "[SELECTED]" if cand['action'] == detail['selected_action'] else ""
        print(f"           - {cand['action'].upper()}: Prob={(cand['predicted_recovery_probability']*100):.1f}%, Cost=₹{cand['action_cost']}, ERV=₹{cand['predicted_erv']} {tag}")
        
    if detail['counterfactual']:
        print("\n[STEP 4] Counterfactual Advantage Calculated:")
        cf = detail['counterfactual']
        print(f"         Selected: {cf['selected_action']} (ERV ₹{cf['selected_erv']})")
        print(f"         Next Best: {cf['counterfactual_action']} (ERV ₹{cf['counterfactual_erv']})")
        print(f"         Economic Advantage: ₹{cf['value_difference']}")
        
    print("\n[STEP 5] Verifying Bounded Execution...")
    print(f"         Execution Status: {detail['latest_execution_status']}")
    exec_type = "Live Razorpay Test API" if RAZORPAY_LIVE_EXECUTION else "Deterministic Local Simulation"
    print(f"         Execution Mechanism: {exec_type}")
    print(f"         Active Payment Link: {detail['active_payment_link_url'] or 'N/A'}")
    
    if not detail['active_payment_link_id']:
        print("\n[DEMO EXIT] Action selected was not a payment link. Cannot demonstrate settlement.")
        return
        
    # 6. Simulate Customer Payment via Settlement Webhook
    link_id = detail['active_payment_link_id']
    print(f"\n[STEP 6] Simulating Customer Payment on {link_id}...")
    
    settlement_payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": link_id,
                    "amount": 100000,
                    "amount_paid": 100000,
                    "status": "paid",
                    "notes": {
                        "transaction_id": transaction_id,
                        "journey_id": journey_id
                    }
                }
            }
        },
        "created_at": int(time.time())
    }
    
    send_webhook("payment_link.paid", settlement_payload)
    
    print("\n[STEP 7] Verifying Reconciliation & Closed-Loop Protection...")
    detail_post = client.get(f"/v1/dashboard/journeys/{journey_id}").json()
    
    print(f"         Final Journey Status: {detail_post['status']}")
    print(f"         Recovered Amount: ₹{detail_post['recovered_amount']}")
    print(f"         Net Value: ₹{detail_post['net_value']}")
    
    print("\n[STEP 8] Audit Timeline:")
    timeline_resp = client.get(f"/v1/dashboard/journeys/{journey_id}/timeline").json()
    for evt in timeline_resp["events"]:
        tag = "[LIVE]" if evt["is_live"] else "[SIMULATED]"
        print(f"         - {evt['timestamp']} {tag} {evt['summary']}")
        
    print("\n==================================================")
    print("GOLDEN DEMO COMPLETE.")
    print("The system successfully recovered the revenue safely.")
    print("==================================================")

if __name__ == "__main__":
    run_golden_demo()
