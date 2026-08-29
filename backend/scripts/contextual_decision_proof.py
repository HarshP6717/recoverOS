import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

import json
from datetime import datetime, timezone
from backend.app.schemas.recovery import DiagnosisRequest
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.providers.llm_provider import MockDiagnosisProvider

def generate_proof():
    provider = MockDiagnosisProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    decision_engine = DecisionEngine(diagnosis_engine=diagnosis_engine)

    scenarios = [
        {
            "id": "Scenario A - Retry",
            "request": DiagnosisRequest(
                transaction_id="tx_proof_A",
                customer_id="cust_proof_1",
                subscription_id="sub_proof_1",
                amount=500.0,
                payment_method="card",
                failure_type="insufficient_funds",
                attempt_number=1, # 1st attempt means 0 previous attempts for AI provider
                days_overdue=1,
                customer_lifetime_value=2000.0,
                previous_failure_count=0 # No past failures triggers 'failures: 0' strong history heuristic
            ),
            "expected_action": "retry_now",
            "context_change": "previous_attempts=0, previous_failure_count=0 (strong history)"
        },
        {
            "id": "Scenario B - Recovery Link",
            "request": DiagnosisRequest(
                transaction_id="tx_proof_B",
                customer_id="cust_proof_1",
                subscription_id="sub_proof_1",
                amount=500.0,
                payment_method="card",
                failure_type="insufficient_funds",
                attempt_number=4, # 4th attempt means 3 previous attempts
                days_overdue=5,
                customer_lifetime_value=2000.0,
                previous_failure_count=3
            ),
            "expected_action": "recovery_link",
            "context_change": "previous_attempts=3, dimishing returns on retry"
        },
        {
            "id": "Scenario C - Escalate Human",
            "request": DiagnosisRequest(
                transaction_id="tx_proof_C",
                customer_id="cust_proof_2",
                subscription_id="sub_proof_2",
                amount=5000.0, # Meaningful financial risk
                payment_method="card",
                failure_type="insufficient_funds",
                attempt_number=2,
                days_overdue=2,
                customer_lifetime_value=15000.0,
                previous_failure_count=99 # 99 triggers the explicit high-risk/uncertainty guardrail in mock
            ),
            "expected_action": "escalate_human",
            "context_change": "previous_failure_count=99 forcing low confidence"
        }
    ]

    results = {
        "metadata": {
            "title": "DETERMINISTIC DEMONSTRATION",
            "description": "Demonstrates contextual decision intelligence without granting AI execution authority.",
            "timestamp": datetime.now(timezone.utc).isoformat()
        },
        "scenarios": []
    }

    for s in scenarios:
        req = s["request"]
        evals, selected_action, status, reason, guardrails, counterfactual = decision_engine.evaluate_request(req)
        
        from backend.app.schemas.diagnosis import DiagnosisRequest as IntelligenceDiagnosisRequest
        intel_req = IntelligenceDiagnosisRequest(
            failure_reason=req.failure_type,
            payment_amount=req.amount,
            payment_method=req.payment_method,
            customer_history=f"CLV: {req.customer_lifetime_value}, Failures: {req.previous_failure_count}",
            previous_attempts=req.attempt_number - 1,
            days_overdue=req.days_overdue,
            journey_round=req.attempt_number
        )
        diagnosis = provider.get_diagnosis(intel_req)

        results["scenarios"].append({
            "scenario": s["id"],
            "context_change": s["context_change"],
            "input_context": {
                "amount": req.amount,
                "failure_type": req.failure_type,
                "previous_attempts": req.attempt_number - 1,
                "previous_failure_count": req.previous_failure_count
            },
            "diagnosis": diagnosis.failure_category,
            "confidence": diagnosis.confidence,
            "candidate_probabilities": diagnosis.recovery_probabilities,
            "candidate_ervs": {ev.action: ev.predicted_erv for ev in evals},
            "selected_action": selected_action,
            "guardrails": guardrails,
            "counterfactual": counterfactual.model_dump() if counterfactual else None,
            "decision_explanation": reason,
            "expected_decision_condition_satisfied": selected_action == s["expected_action"]
        })

    out_dir = Path(__file__).resolve().parent.parent.parent / "evaluation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "contextual_decision_proof.json"
    
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"Deterministic contextual proof written to {out_file}")

if __name__ == "__main__":
    generate_proof()
