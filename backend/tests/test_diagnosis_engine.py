import pytest
import httpx
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

from backend.app.schemas.diagnosis import DiagnosisRequest, DiagnosisResponse
from backend.app.providers.llm_provider import MockDiagnosisProvider, GeminiDiagnosisProvider
from backend.app.services.diagnosis_engine import DiagnosisEngine

# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

def test_diagnosis_request_defaults():
    req = DiagnosisRequest(
        failure_reason="timeout",
        payment_amount=100.0,
        payment_method="upi"
    )
    assert req.previous_attempts == 0
    assert req.journey_round == 1


def test_diagnosis_response_valid():
    resp = DiagnosisResponse(
        failure_category="bank_timeout",
        confidence=0.8,
        evidence=["some evidence"],
        recovery_probabilities={
            "payment_link": 0.5,
            "retry": 0.5,
            "escalate": 0.0,
            "no_action": 0.0
        },
        reasoning_summary="timeout detected"
    )
    assert resp.failure_category == "bank_timeout"
    assert resp.confidence == 0.8


def test_diagnosis_response_invalid_confidence():
    with pytest.raises(ValidationError):
        DiagnosisResponse(
            failure_category="bank_timeout",
            confidence=1.5,  # Invalid: > 1.0
            evidence=["some evidence"],
            recovery_probabilities={"payment_link": 0.5, "retry": 0.5, "escalate": 0.0, "no_action": 0.0},
            reasoning_summary="timeout detected"
        )


def test_diagnosis_response_invalid_category():
    with pytest.raises(ValidationError):
        DiagnosisResponse(
            failure_category="magic_error",  # Not in Literal
            confidence=0.8,
            evidence=["some evidence"],
            recovery_probabilities={"payment_link": 0.5, "retry": 0.5, "escalate": 0.0, "no_action": 0.0},
            reasoning_summary="timeout detected"
        )

# ---------------------------------------------------------------------------
# Mock Provider Tests
# ---------------------------------------------------------------------------

def test_mock_provider_deterministic_behavior():
    provider = MockDiagnosisProvider()
    
    # Test insufficient funds
    req1 = DiagnosisRequest(failure_reason="insufficient funds in account", payment_amount=100.0, payment_method="card")
    resp1 = provider.get_diagnosis(req1)
    assert resp1.failure_category == "insufficient_funds"
    assert resp1.confidence == 0.91
    
    # Test timeout
    req2 = DiagnosisRequest(failure_reason="bank timeout occurred", payment_amount=100.0, payment_method="upi")
    resp2 = provider.get_diagnosis(req2)
    assert resp2.failure_category == "bank_timeout"
    assert resp2.confidence == 0.85
    
    # Test identical input produces identical output
    resp2_dup = provider.get_diagnosis(req2)
    assert resp2.model_dump() == resp2_dup.model_dump()


# ---------------------------------------------------------------------------
# Diagnosis Engine Tests
# ---------------------------------------------------------------------------

def test_diagnosis_engine_success():
    engine = DiagnosisEngine(provider=MockDiagnosisProvider())
    req = DiagnosisRequest(failure_reason="insufficient funds", payment_amount=100.0, payment_method="card")
    resp = engine.diagnose_failure(req)
    assert resp.failure_category == "insufficient_funds"


def test_diagnosis_engine_provider_exception_triggers_fallback():
    mock_provider = MagicMock()
    mock_provider.get_diagnosis.side_effect = Exception("Network Down")
    
    engine = DiagnosisEngine(provider=mock_provider)
    req = DiagnosisRequest(failure_reason="insufficient funds", payment_amount=100.0, payment_method="card")
    
    # Should not raise exception, but return fallback
    resp = engine.diagnose_failure(req)
    
    assert resp.failure_category == "unknown"
    assert resp.confidence == 0.1
    assert "Provider unavailable or failed." in resp.evidence[0]


def test_diagnosis_engine_schema_failure_triggers_fallback():
    # A provider that returns a dict instead of DiagnosisResponse
    class BadProvider:
        def get_diagnosis(self, request):
            # This doesn't match the DiagnosisResponse shape
            raise ValidationError.from_exception_data("Validation failed", line_errors=[])

    engine = DiagnosisEngine(provider=BadProvider())
    req = DiagnosisRequest(failure_reason="error", payment_amount=100.0, payment_method="card")
    
    # Must fallback safely
    resp = engine.diagnose_failure(req)
    assert resp.confidence == 0.1
    assert resp.failure_category == "unknown"

# ---------------------------------------------------------------------------
# Gemini Provider Tests
# ---------------------------------------------------------------------------

@patch.dict('os.environ', clear=True)
def test_gemini_provider_missing_key():
    provider = GeminiDiagnosisProvider()
    req = DiagnosisRequest(failure_reason="test", payment_amount=100.0, payment_method="card")
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        provider.get_diagnosis(req)


@patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'})
@patch('httpx.Client.post')
def test_gemini_provider_success(mock_post):
    provider = GeminiDiagnosisProvider()
    
    # Mock successful Gemini JSON response
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": '{"failure_category": "insufficient_funds", "confidence": 0.95, "evidence": ["low balance"], "recovery_probabilities": {"payment_link": 0.8, "retry": 0.2, "escalate": 0.0, "no_action": 0.0}, "reasoning_summary": "Test"}'
                }]
            }
        }]
    }
    mock_post.return_value = mock_response
    
    req = DiagnosisRequest(failure_reason="low balance", payment_amount=100.0, payment_method="card")
    resp = provider.get_diagnosis(req)
    
    assert resp.failure_category == "insufficient_funds"
    assert resp.confidence == 0.95


@patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'})
@patch('httpx.Client.post')
def test_gemini_provider_timeout(mock_post):
    provider = GeminiDiagnosisProvider()
    mock_post.side_effect = httpx.RequestError("Timeout")
    
    req = DiagnosisRequest(failure_reason="timeout", payment_amount=100.0, payment_method="card")
    
    with pytest.raises(RuntimeError, match="Gemini API error: Timeout"):
        provider.get_diagnosis(req)


@patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'})
@patch('httpx.Client.post')
def test_gemini_provider_malformed_json(mock_post):
    provider = GeminiDiagnosisProvider()
    
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": '{"broken_json": true, ' # syntax error
                }]
            }
        }]
    }
    mock_post.return_value = mock_response
    
    req = DiagnosisRequest(failure_reason="low balance", payment_amount=100.0, payment_method="card")
    
    with pytest.raises(RuntimeError, match="JSON parsing error"):
        provider.get_diagnosis(req)

