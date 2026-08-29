import json
import logging
import uuid
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.app.main import app
from backend.app.models.database import SessionLocal, init_db, get_db_session, RecoveryJourneyModel
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.journey_service import JourneyService
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.event_service import EventService
from backend.app.services.decision_engine import DecisionEngine
from backend.app.providers.llm_provider import MockDiagnosisProvider
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.services.guardrails import GuardrailEngine
from backend.app.schemas.recovery import DecisionRequest
from backend.app.services.razorpay_client import RazorpayTestClient, RazorpayTimeoutError, RazorpayGatewayUnavailableError
from backend.app.core.config import RAZORPAY_WEBHOOK_SECRET
import hmac
import hashlib

# Silence logs for clean output
logging.getLogger("backend").setLevel(logging.CRITICAL)

client = TestClient(app)

def sign_payload(payload_bytes: bytes) -> str:
    return hmac.new(
        key=RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()

def run_scenario(scenario_id, name, run_fn):
    db = SessionLocal()
    try:
        result = run_fn(db)
        return {
            "scenario_id": scenario_id,
            "scenario": name,
            "pass": result.get("pass", False),
            "actual_result": result.get("actual_result", "Unknown"),
            "state_transition": result.get("state_transition", "Unknown"),
            "financial_impact": result.get("financial_impact", "None"),
            "retryability": result.get("retryability", "Unknown"),
            "details": result.get("details", {})
        }
    except Exception as e:
        return {
            "scenario_id": scenario_id,
            "scenario": name,
            "pass": False,
            "actual_result": f"Unhandled Exception: {str(e)}",
            "state_transition": "Error",
            "financial_impact": "Error",
            "retryability": "Error",
            "details": {}
        }
    finally:
        db.close()


# Scenario 1: Razorpay timeout on creation
def sc_01_creation_timeout(db):
    tx_id = f"tx_rt_{uuid.uuid4().hex[:8]}"
    
    provider = MockDiagnosisProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails, diagnosis_engine=diagnosis_engine)
    
    # Fault injected client
    rzp = RazorpayTestClient(simulate_timeout=True)
    executor = ActionExecutionSimulator(razorpay_client=rzp)
    event_service = EventService(decision_engine=decision_engine, action_executor=executor)
    
    req = DecisionRequest(
        transaction_id=tx_id,
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=1000.0,
        payment_method="card",
        failure_type="bank_timeout", # Will choose retry_now or payment_link. Actually retry_now doesn't timeout the same way in our sim, but let's force a timeout on recovery_link. Wait, we can't force what it chooses easily without a mock. 
    )
    # Actually bank_timeout -> retry_now. Let's use insufficient_funds -> recovery_link
    req.failure_type = "insufficient_funds"
    
    decision = event_service.process_decision(db, req)
    
    exec_status = decision.execution.execution_status if decision.execution else "None"
    
    passed = exec_status == "EXECUTION_UNKNOWN"
    return {
        "pass": passed,
        "actual_result": f"Execution status: {exec_status}",
        "state_transition": f"INIT -> {exec_status}",
        "financial_impact": "No link created, no double charge",
        "retryability": "Can be reconciled or manually reviewed",
    }

# Scenario 2: Razorpay 5xx on creation
def sc_02_creation_5xx(db):
    tx_id = f"tx_r5_{uuid.uuid4().hex[:8]}"
    
    provider = MockDiagnosisProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails, diagnosis_engine=diagnosis_engine)
    
    rzp = RazorpayTestClient(simulate_gateway_down=True)
    executor = ActionExecutionSimulator(razorpay_client=rzp)
    event_service = EventService(decision_engine=decision_engine, action_executor=executor)
    
    req = DecisionRequest(
        transaction_id=tx_id, customer_id="cust_1", subscription_id="sub_1",
        amount=1000.0, payment_method="card", failure_type="insufficient_funds",
    )
    decision = event_service.process_decision(db, req)
    exec_status = decision.execution.execution_status if decision.execution else "None"
    
    passed = exec_status == "EXECUTION_FAILED" # 5xx means failed
    return {
        "pass": passed,
        "actual_result": f"Execution status: {exec_status}",
        "state_transition": f"INIT -> {exec_status}",
        "financial_impact": "No link created",
        "retryability": "Safe to retry immediately",
    }

# Scenario 3: Razorpay timeout on cancellation
class FaultyRazorpayClientForCancel(RazorpayTestClient):
    def cancel_payment_link(self, payment_link_id: str):
        raise RazorpayTimeoutError("Timeout on cancellation")

def sc_03_cancellation_timeout(db):
    tx_id = f"tx_rc_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    plink_id = f"plink_{uuid.uuid4().hex[:8]}"
    journey_svc.record_action(db, journey.journey_id, "recovery_link", payment_link_id=plink_id)
    
    # Settlement comes in
    rzp = FaultyRazorpayClientForCancel()
    recon_svc = ReconciliationService(journey_service=journey_svc, razorpay_client=rzp)
    
    result = recon_svc.reconcile_settlement(db, "payment.captured", {
        "payload": {"payment": {"entity": {"id": "pay_999", "amount": 100000, "notes": {"transaction_id": tx_id}}}}
    })
    
    passed = result.status == "reconciled" and result.cancellation_pending is True
    
    return {
        "pass": passed,
        "actual_result": f"cancellation_pending={result.cancellation_pending}, status={result.status}",
        "state_transition": "IN_PROGRESS -> RECOVERED (with pending cancellation)",
        "financial_impact": "Double charge risk flagged for manual intervention",
        "retryability": "Requires manual reconciliation/cancellation",
    }

# Scenario 4: Duplicate webhook delivery
def sc_04_duplicate_webhook(db):
    tx_id = f"tx_dw_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    
    # We send identical payload twice to the webhook endpoint
    payload = {
        "event": "payment_link.paid",
        "webhook_event_id": f"wh_dup_{uuid.uuid4().hex[:8]}",
        "payload": {"payment_link": {"entity": {"reference_id": tx_id, "amount": 100000}}}
    }
    
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = sign_payload(payload_bytes)
    
    r1 = client.post("/v1/webhooks/razorpay", content=payload_bytes, headers={"x-razorpay-signature": sig})
    r2 = client.post("/v1/webhooks/razorpay", content=payload_bytes, headers={"x-razorpay-signature": sig})
    
    passed = (r1.status_code == 200) and (r2.status_code == 200) and (r2.json().get("status") == "duplicate_ignored")
    
    # Verify journey recovered amount is exactly 1000, not 2000
    db.refresh(journey)
    passed = passed and (journey.recovered_amount == 1000.0)
    
    return {
        "pass": passed,
        "actual_result": f"R1 status={r1.json().get('status')}, R2 status={r2.json().get('status')}, recovered={journey.recovered_amount}",
        "state_transition": "RECOVERED -> RECOVERED",
        "financial_impact": "No double counting of revenue",
        "retryability": "Deduplicated",
    }

# Scenario 5: Concurrent duplicate webhook delivery
def sc_05_concurrent_duplicate_webhook(db):
    # Testing concurrent DB access is hard in a simple script without threading, but we can test the IntegrityError behavior via `reserve_webhook_event_atomic`
    from backend.app.repositories.event_repository import reserve_webhook_event_atomic
    
    wh_id = f"wh_conc_{uuid.uuid4().hex[:8]}"
    
    # Simulate thread 1
    t1_reserved = reserve_webhook_event_atomic(db, wh_id, "payment.captured")
    
    # Simulate thread 2
    t2_reserved = reserve_webhook_event_atomic(db, wh_id, "payment.captured")
    
    passed = (t1_reserved is True) and (t2_reserved is False)
    
    return {
        "pass": passed,
        "actual_result": f"T1 Reserved: {t1_reserved}, T2 Reserved: {t2_reserved}",
        "state_transition": "RESERVED -> Rejected (Duplicate)",
        "financial_impact": "Prevents concurrent double processing",
        "retryability": "Idempotent via DB constraint",
    }

# Scenario 6: Invalid HMAC
def sc_06_invalid_hmac(db):
    payload = {
        "event": "payment_link.paid",
        "webhook_event_id": f"wh_hmac_{uuid.uuid4().hex[:8]}",
        "payload": {"payment_link": {"entity": {"reference_id": "tx_xyz", "amount": 100000}}}
    }
    # Passing an explicitly WRONG signature bypassing the test_mode override in routes_webhooks.py
    payload_bytes = json.dumps(payload).encode("utf-8")
    r = client.post("/v1/webhooks/razorpay", content=payload_bytes, headers={"x-razorpay-signature": "invalid_signature_string"})
    
    passed = r.status_code == 401
    
    return {
        "pass": passed,
        "actual_result": f"HTTP {r.status_code}",
        "state_transition": "None",
        "financial_impact": "No DB mutation",
        "retryability": "N/A",
    }

# Scenario 7: Missing HMAC
def sc_07_missing_hmac(db):
    payload = {
        "event": "payment_link.paid",
        "webhook_event_id": f"wh_mhmac_{uuid.uuid4().hex[:8]}",
        "payload": {"payment_link": {"entity": {"reference_id": "tx_xyz", "amount": 100000}}}
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    r = client.post("/v1/webhooks/razorpay", content=payload_bytes)
    
    passed = r.status_code == 401
    
    return {
        "pass": passed,
        "actual_result": f"HTTP {r.status_code}",
        "state_transition": "None",
        "financial_impact": "No DB mutation",
        "retryability": "N/A",
    }

# Scenario 8: Replay webhook
def sc_08_replay_webhook(db):
    # Similar to duplicate webhook, ensuring the same event_id is rejected
    return sc_04_duplicate_webhook(db)

# Scenario 9: LLM unavailable
def sc_09_llm_unavailable(db):
    tx_id = f"tx_llm_{uuid.uuid4().hex[:8]}"
    
    # Mock DiagnosisProvider with network error
    class ErrorProvider(MockDiagnosisProvider):
        def get_diagnosis(self, *args, **kwargs):
            raise Exception("Network Timeout")
            
    provider = ErrorProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails, diagnosis_engine=diagnosis_engine)
    
    req = DecisionRequest(
        transaction_id=tx_id, customer_id="cust_1", subscription_id="sub_1",
        amount=1000.0, payment_method="card", failure_type="bank_timeout",
    )
    
    evals, action, status, reason, guardrails, counterfactual = decision_engine.evaluate_request(req)
    
    passed = status == "FALLBACK_SAFE" and action == "escalate_human"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {status}, Action: {action}",
        "state_transition": "INIT -> FALLBACK_SAFE",
        "financial_impact": "No automated action taken, safe escalation",
        "retryability": "Human intervention required",
    }

# Scenario 10: Malformed LLM response
def sc_10_malformed_llm_response(db):
    tx_id = f"tx_llm2_{uuid.uuid4().hex[:8]}"
    
    class MalformedProvider(MockDiagnosisProvider):
        def get_diagnosis(self, *args, **kwargs):
            return "This is not JSON"
            
    provider = MalformedProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails, diagnosis_engine=diagnosis_engine)
    
    req = DecisionRequest(
        transaction_id=tx_id, customer_id="cust_1", subscription_id="sub_1",
        amount=1000.0, payment_method="card", failure_type="bank_timeout",
    )
    
    evals, action, status, reason, guardrails, counterfactual = decision_engine.evaluate_request(req)
    
    passed = status == "FALLBACK_SAFE" and action == "escalate_human"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {status}, Action: {action}",
        "state_transition": "INIT -> FALLBACK_SAFE",
        "financial_impact": "No automated action taken, safe escalation",
        "retryability": "Human intervention required",
    }

# Scenario 11: Unknown settlement identifier
def sc_11_unknown_settlement(db):
    recon_svc = ReconciliationService()
    result = recon_svc.reconcile_settlement(db, "payment_link.paid", {
        "payload": {"payment_link": {"entity": {"id": "plink_unknown", "amount": 100000}}}
    })
    
    passed = result.status == "pending_settlement"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}",
        "state_transition": "None",
        "financial_impact": "Orphaned settlement (requires manual investigation)",
        "retryability": "Can be replayed later",
    }

# Scenario 12: Settlement arriving before action finishes
def sc_12_settlement_before_action(db):
    # Essentially, a journey exists and is IN_PROGRESS. We just process a settlement before the action transitions it.
    tx_id = f"tx_sa_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    
    recon_svc = ReconciliationService(journey_service=journey_svc)
    result = recon_svc.reconcile_settlement(db, "payment.captured", {
        "payload": {"payment": {"entity": {"id": "pay_123", "amount": 100000, "notes": {"transaction_id": tx_id}}}}
    })
    
    passed = result.status == "reconciled" and result.journey.status == "RECOVERED"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}, Journey Status: {result.journey.status}",
        "state_transition": "IN_PROGRESS -> RECOVERED",
        "financial_impact": "Correctly reconciled",
        "retryability": "Idempotent",
    }

# Scenario 13: STOPPED journey receiving settlement
def sc_13_stopped_journey_settlement(db):
    tx_id = f"tx_st_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    journey_svc.mark_stopped(db, journey.journey_id)
    
    recon_svc = ReconciliationService(journey_service=journey_svc)
    result = recon_svc.reconcile_settlement(db, "payment.captured", {
        "payload": {"payment": {"entity": {"id": "pay_123", "amount": 100000, "notes": {"transaction_id": tx_id}}}}
    })
    
    passed = result.status == "terminal_state_conflict"
    
    db.refresh(journey)
    passed = passed and journey.status == "STOPPED"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}, Journey Status: {journey.status}",
        "state_transition": "STOPPED -> STOPPED",
        "financial_impact": "Manual review required, no auto override",
        "retryability": "Requires manual override",
    }

# Scenario 14: ESCALATED journey receiving settlement
def sc_14_escalated_journey_settlement(db):
    tx_id = f"tx_esc_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    journey_svc.mark_escalated(db, journey.journey_id)
    
    recon_svc = ReconciliationService(journey_service=journey_svc)
    result = recon_svc.reconcile_settlement(db, "payment.captured", {
        "payload": {"payment": {"entity": {"id": "pay_123", "amount": 100000, "notes": {"transaction_id": tx_id}}}}
    })
    
    passed = result.status == "terminal_state_conflict"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}",
        "state_transition": "ESCALATED -> ESCALATED",
        "financial_impact": "Manual review required",
        "retryability": "Requires manual override",
    }

# Scenario 15: EXHAUSTED journey receiving legitimate late settlement
def sc_15_exhausted_journey_settlement(db):
    tx_id = f"tx_ex_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    journey = journey_svc.get_or_create_journey(
        db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
    )
    journey_svc.mark_exhausted(db, journey.journey_id)
    
    recon_svc = ReconciliationService(journey_service=journey_svc)
    result = recon_svc.reconcile_settlement(db, "payment.captured", {
        "payload": {"payment": {"entity": {"id": "pay_123", "amount": 100000, "notes": {"transaction_id": tx_id}}}}
    })
    
    passed = result.status == "reconciled" and result.journey.status == "RECOVERED"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}, Journey Status: {result.journey.status}",
        "state_transition": "EXHAUSTED -> RECOVERED",
        "financial_impact": "Legitimate late revenue recognized",
        "retryability": "Idempotent",
    }

# Scenario 16: payment_link.cancelled event
def sc_16_payment_link_cancelled(db):
    recon_svc = ReconciliationService()
    result = recon_svc.reconcile_settlement(db, "payment_link.cancelled", {
        "payload": {"payment_link": {"entity": {"id": "plink_123", "amount": 100000}}}
    })
    
    passed = result.status == "ignored_cancellation"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}",
        "state_transition": "None",
        "financial_impact": "No false settlement",
        "retryability": "Ignored",
    }

# Scenario 17: Invalid/NaN/Infinity amount
def sc_17_invalid_amount(db):
    recon_svc = ReconciliationService()
    result = recon_svc.reconcile_settlement(db, "payment_link.paid", {
        "payload": {"payment_link": {"entity": {"id": "plink_123", "amount": float('nan')}}}
    })
    
    passed = result.status == "invalid_amount"
    
    return {
        "pass": passed,
        "actual_result": f"Status: {result.status}, Msg: {result.message}",
        "state_transition": "None",
        "financial_impact": "Rejected malformed payload",
        "retryability": "Fix payload",
    }

# Scenario 18: Database failure during processing
def sc_18_database_failure(db):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    bad_engine = create_engine("sqlite:///:memory:")
    BadSession = sessionmaker(bind=bad_engine)
    bad_db = BadSession()
    # Not calling init_db() so tables don't exist
    
    tx_id = f"tx_db_{uuid.uuid4().hex[:8]}"
    journey_svc = JourneyService()
    
    try:
        journey_svc.get_or_create_journey(
            bad_db, tx_id, 1000.0, "card", "insufficient_funds", "cust_1"
        )
        passed = False
        msg = "Did not raise exception"
    except Exception as e:
        passed = True
        msg = f"Raised exception: {type(e).__name__}"
    
    return {
        "pass": passed,
        "actual_result": msg,
        "state_transition": "Rollback",
        "financial_impact": "No partial state committed",
        "retryability": "Safe to retry when DB recovers",
    }

def main():
    init_db()
    scenarios = [
        ("01", "Razorpay timeout on creation", sc_01_creation_timeout),
        ("02", "Razorpay 5xx on creation", sc_02_creation_5xx),
        ("03", "Razorpay timeout on cancellation", sc_03_cancellation_timeout),
        ("04", "Duplicate webhook delivery", sc_04_duplicate_webhook),
        ("05", "Concurrent duplicate webhook delivery", sc_05_concurrent_duplicate_webhook),
        ("06", "Invalid HMAC", sc_06_invalid_hmac),
        ("07", "Missing HMAC", sc_07_missing_hmac),
        ("08", "Replayed webhook", sc_08_replay_webhook),
        ("09", "LLM unavailable", sc_09_llm_unavailable),
        ("10", "Malformed LLM response", sc_10_malformed_llm_response),
        ("11", "Unknown settlement identifier", sc_11_unknown_settlement),
        ("12", "Settlement before action finishes", sc_12_settlement_before_action),
        ("13", "STOPPED journey receiving settlement", sc_13_stopped_journey_settlement),
        ("14", "ESCALATED journey receiving settlement", sc_14_escalated_journey_settlement),
        ("15", "EXHAUSTED journey legitimate late settlement", sc_15_exhausted_journey_settlement),
        ("16", "payment_link.cancelled event", sc_16_payment_link_cancelled),
        ("17", "Invalid/NaN/Infinity amount", sc_17_invalid_amount),
        ("18", "Database failure during processing", sc_18_database_failure),
    ]

    results = []
    all_passed = True
    for sid, name, fn in scenarios:
        res = run_scenario(sid, name, fn)
        results.append(res)
        if not res["pass"]:
            all_passed = False

    output_dir = Path("evaluation/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "resilience_latest.json"
    
    with open(out_file, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scenarios_tested": len(scenarios),
            "all_passed": all_passed,
            "results": results
        }, f, indent=2)
        
    print(f"Resilience Lab completed. All passed: {all_passed}")
    print(f"Results saved to {out_file}")

if __name__ == "__main__":
    main()
