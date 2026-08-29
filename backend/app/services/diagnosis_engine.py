import logging
from pydantic import ValidationError
from backend.app.schemas.diagnosis import DiagnosisRequest, DiagnosisResponse
from backend.app.providers.llm_provider import DiagnosisProvider, MockDiagnosisProvider

logger = logging.getLogger(__name__)

class DiagnosisEngine:
    """
    Payment Intelligence Engine (Checkpoint 2).
    Analyzes payment failures through an injected DiagnosisProvider (Mock or Gemini).
    Enforces zero execution authority by only returning a DiagnosisResponse.
    Implements a strict, deterministic fallback on any provider failure or validation error.
    """
    def __init__(self, provider: DiagnosisProvider = None):
        self.provider = provider or MockDiagnosisProvider()

    def diagnose_failure(self, request: DiagnosisRequest) -> DiagnosisResponse:
        """
        Diagnose the failure using the provider. 
        Guarantees a valid DiagnosisResponse even if the underlying provider fails.
        """
        try:
            logger.info(f"Requesting diagnosis for failure_reason: '{request.failure_reason}'")
            diagnosis = self.provider.get_diagnosis(request)
            logger.info(f"Diagnosis successful: category={diagnosis.failure_category}, confidence={diagnosis.confidence}")
            return diagnosis
            
        except ValidationError as e:
            logger.error(f"Provider returned invalid diagnosis schema: {e}. Falling back.")
            return self._fallback_diagnosis(request, str(e))
            
        except Exception as e:
            logger.error(f"Provider execution failed: {e}. Falling back.")
            return self._fallback_diagnosis(request, str(e))

    def _fallback_diagnosis(self, request: DiagnosisRequest, error_msg: str) -> DiagnosisResponse:
        """
        Deterministic, pessimistic fallback diagnosis.
        Ensures the system never crashes due to an LLM failure, but also 
        ensures the resulting diagnosis is highly likely to be rejected by guardrails 
        (low confidence, 'unknown' category).
        """
        return DiagnosisResponse(
            failure_category="unknown",
            confidence=0.1,  # Pessimistic confidence -> will trigger guardrails
            evidence=[
                "Provider unavailable or failed.",
                f"Error: {error_msg}",
                "Using deterministic fallback safe diagnosis."
            ],
            recovery_probabilities={
                "payment_link": 0.1, 
                "retry": 0.1, 
                "escalate": 0.9, 
                "no_action": 0.0
            },
            reasoning_summary="Provider error triggered deterministic fallback. Escalation recommended."
        )
