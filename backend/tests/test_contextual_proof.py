import pytest
import inspect
from backend.app.schemas.recovery import DiagnosisRequest
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.providers.llm_provider import MockDiagnosisProvider

def test_same_context_deterministic_result():
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    req = DiagnosisRequest(
        transaction_id="tx_test",
        customer_id="cust_test",
        subscription_id="sub_test",
        amount=500.0,
        payment_method="card",
        failure_type="insufficient_funds",
        attempt_number=1,
        days_overdue=1,
        customer_lifetime_value=2000.0,
        previous_failure_count=0
    )
    
    _, action1, _, _, _, _ = engine.evaluate_request(req)
    _, action2, _, _, _, _ = engine.evaluate_request(req)
    
    assert action1 == action2
    assert action1 == "retry_now"

def test_context_change_changes_action():
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    # Scenario A: Low attempts, good history
    req_a = DiagnosisRequest(
        transaction_id="tx_a", customer_id="c_1", subscription_id="s_1",
        amount=500.0, payment_method="card", failure_type="insufficient_funds",
        attempt_number=1, days_overdue=1, customer_lifetime_value=2000.0,
        previous_failure_count=0
    )
    evals_a, action_a, _, _, _, _ = engine.evaluate_request(req_a)
    
    # Scenario B: High attempts, no good history
    req_b = DiagnosisRequest(
        transaction_id="tx_b", customer_id="c_2", subscription_id="s_2",
        amount=500.0, payment_method="card", failure_type="insufficient_funds",
        attempt_number=4, days_overdue=5, customer_lifetime_value=2000.0,
        previous_failure_count=3
    )
    evals_b, action_b, _, _, _, _ = engine.evaluate_request(req_b)
    
    assert action_a == "retry_now"
    assert action_b == "recovery_link"
    
    # Verify the causal chain
    retry_erv_a = next(e.predicted_erv for e in evals_a if e.action == "retry_now")
    link_erv_a = next(e.predicted_erv for e in evals_a if e.action == "recovery_link")
    assert retry_erv_a > link_erv_a
    
    retry_erv_b = next(e.predicted_erv for e in evals_b if e.action == "retry_now")
    link_erv_b = next(e.predicted_erv for e in evals_b if e.action == "recovery_link")
    assert link_erv_b > retry_erv_b

def test_ai_output_contains_no_execution_commands():
    from backend.app.schemas.diagnosis import DiagnosisResponse
    
    # Verify schema fields - it should only have probabilities and text, no commands
    fields = DiagnosisResponse.model_fields.keys()
    assert "execute" not in fields
    assert "command" not in fields
    assert "payment_link_url" not in fields

def test_ai_cannot_access_razorpay_client():
    # Verify that the DiagnosisEngine and Providers do not import Razorpay
    import importlib
    
    import backend.app.providers.llm_provider as provider_module
    import backend.app.services.diagnosis_engine as engine_module
    
    # Static checking of imports
    provider_source = inspect.getsource(provider_module)
    assert "razorpay" not in provider_source.lower()
    
    engine_source = inspect.getsource(engine_module)
    assert "razorpay" not in engine_source.lower()

def test_low_confidence_activates_guardrail():
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    req = DiagnosisRequest(
        transaction_id="tx_c", customer_id="c_3", subscription_id="s_3",
        amount=5000.0, payment_method="card", failure_type="insufficient_funds",
        attempt_number=2, days_overdue=2, customer_lifetime_value=15000.0,
        previous_failure_count=99
    )
    
    _, action, _, reason, guardrails, _ = engine.evaluate_request(req)
    
    assert "LOW_AI_CONFIDENCE" in guardrails
    assert action in ["escalate_human", "stop"]

def test_erv_determines_economic_ranking():
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    req = DiagnosisRequest(
        transaction_id="tx_a", customer_id="c_1", subscription_id="s_1",
        amount=500.0, payment_method="card", failure_type="insufficient_funds",
        attempt_number=1, days_overdue=1, customer_lifetime_value=2000.0,
        previous_failure_count=0
    )
    
    evals, selected_action, _, _, _, _ = engine.evaluate_request(req)
    
    # Get highest ERV among allowed actions
    allowed_evals = [e for e in evals if e.allowed]
    max_erv = max(e.predicted_erv for e in allowed_evals)
    
    selected_ev = next(e for e in allowed_evals if e.action == selected_action)
    assert selected_ev.predicted_erv == max_erv

def test_counterfactual_remains_correct():
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    req = DiagnosisRequest(
        transaction_id="tx_a", customer_id="c_1", subscription_id="s_1",
        amount=500.0, payment_method="card", failure_type="insufficient_funds",
        attempt_number=1, days_overdue=1, customer_lifetime_value=2000.0,
        previous_failure_count=0
    )
    
    _, selected_action, _, _, _, counterfactual = engine.evaluate_request(req)
    
    assert counterfactual is not None
    assert counterfactual.selected_action == selected_action
    assert counterfactual.counterfactual_action != selected_action
    assert counterfactual.value_difference >= 0

def test_existing_business_logic_not_bypassed():
    # If we pass in a negative ERV scenario (e.g. amount is very small), 
    # it should hit NEGATIVE_ERV_PROTECTION guardrail.
    provider = MockDiagnosisProvider()
    engine = DecisionEngine(diagnosis_engine=DiagnosisEngine(provider=provider))
    
    req = DiagnosisRequest(
        transaction_id="tx_a", customer_id="c_1", subscription_id="s_1",
        amount=5.0, # Tiny amount
        payment_method="card", failure_type="insufficient_funds",
        attempt_number=1, days_overdue=1, customer_lifetime_value=2000.0,
        previous_failure_count=0
    )
    
    _, _, _, _, guardrails, _ = engine.evaluate_request(req)
    assert "NEGATIVE_ERV_PROTECTION" in guardrails
