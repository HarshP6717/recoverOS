import pytest
from backend.app.schemas.recovery import DiagnosisRequest
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.providers.llm_provider import MockDiagnosisProvider


@pytest.fixture
def decision_engine():
    provider = MockDiagnosisProvider()
    diagnosis_engine = DiagnosisEngine(provider=provider)
    guardrails = GuardrailEngine()
    return DecisionEngine(guardrail_engine=guardrails, diagnosis_engine=diagnosis_engine)


def test_counterfactual_selected_action_excluded(decision_engine):
    req = DiagnosisRequest(
        transaction_id="tx_cf_1",
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=1500.0,
        payment_method="card",
        failure_type="bank_timeout",
    )
    
    # bank_timeout -> confidence 0.85, retry=0.88, payment_link=0.1
    # retry_now is selected (ERV ~ 1317)
    
    evals, action, status, reason, guardrails, counterfactual = decision_engine.evaluate_request(req)
    
    assert action == "retry_now"
    assert counterfactual is not None
    assert counterfactual.selected_action == "retry_now"
    
    # Counterfactual should be retry_later or send_reminder depending on the ERV
    assert counterfactual.counterfactual_action != "retry_now"
    assert counterfactual.value_difference >= 0


def test_counterfactual_low_confidence_fallback(decision_engine):
    req = DiagnosisRequest(
        transaction_id="tx_cf_2",
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=5000.0,
        payment_method="card",
        failure_type="low_confidence_test",
    )
    
    # low_confidence_test -> confidence 0.40 -> forces escalate_human or stop
    # Escalate ERV = 5000 * 0.5 - 30 - 5 = 2465
    
    evals, action, status, reason, guardrails, counterfactual = decision_engine.evaluate_request(req)
    
    assert action == "escalate_human"
    assert "LOW_AI_CONFIDENCE" in guardrails
    assert counterfactual is not None
    
    # Next best allowed action when escalate_human is removed is 'stop'
    assert counterfactual.counterfactual_action == "stop"
    assert counterfactual.counterfactual_erv == 0.0
    assert counterfactual.value_difference == 2465.0
