import abc
import os
import json
import logging
import httpx
from typing import Optional

from backend.app.schemas.diagnosis import DiagnosisRequest, DiagnosisResponse

logger = logging.getLogger(__name__)

class DiagnosisProvider(abc.ABC):
    @abc.abstractmethod
    def get_diagnosis(self, request: DiagnosisRequest) -> DiagnosisResponse:
        pass


class MockDiagnosisProvider(DiagnosisProvider):
    """
    Deterministic mock provider for testing and safe fallbacks.
    Identical input strictly produces identical output.
    """
    def get_diagnosis(self, request: DiagnosisRequest) -> DiagnosisResponse:
        reason = request.failure_reason.lower()
        
        # Deterministic mapping
        if "insufficient" in reason or "funds" in reason:
            cat = "insufficient_funds"
            conf = 0.91
            probs = {"payment_link": 0.72, "retry": 0.41, "escalate": 0.08, "no_action": 0.0}
        elif "timeout" in reason or "bank" in reason:
            cat = "bank_timeout"
            conf = 0.85
            probs = {"payment_link": 0.50, "retry": 0.88, "escalate": 0.05, "no_action": 0.0}
        elif "fraud" in reason:
            cat = "fraud_suspected"
            conf = 0.98
            probs = {"payment_link": 0.05, "retry": 0.01, "escalate": 0.99, "no_action": 0.0}
        elif "expired" in reason:
            cat = "expired_card"
            conf = 0.90
            probs = {"payment_link": 0.85, "retry": 0.05, "escalate": 0.10, "no_action": 0.0}
        elif "low_confidence_test" in reason:
            cat = "unknown"
            conf = 0.40
            probs = {"payment_link": 0.50, "retry": 0.50, "escalate": 0.50, "no_action": 0.0}
        else:
            cat = "unknown"
            conf = 0.75
            probs = {"payment_link": 0.50, "retry": 0.50, "escalate": 0.50, "no_action": 0.0}
            
        # --- DETERMINISTIC CONTEXTUAL MODIFIERS ---
        # Simulates context awareness strictly through documented probability shifts
        # 1. Fatigue / Diminishing Returns
        if request.previous_attempts >= 2:
            probs["retry"] = max(0.01, probs["retry"] - (0.20 * request.previous_attempts))
            probs["payment_link"] = min(0.99, probs["payment_link"] + (0.05 * request.previous_attempts))
            
        # 2. Customer History Context (DecisionEngine formats this as 'CLV: X, Failures: Y')
        history = request.customer_history.lower()
        if "failures: 0" in history and request.previous_attempts == 0:
            # Strong history (no past failures) + new failure = highly likely to be a soft error, boost retry
            probs["retry"] = min(0.99, probs["retry"] + 0.35)
            
        # 3. High Risk / Uncertainty
        if "failures: 99" in history or "contradictory" in history:
            # An unusually high failure count or explicit contradictory flag triggers risk guardrails
            conf = 0.40
            
        return DiagnosisResponse(
            failure_category=cat,
            confidence=conf,
            evidence=[
                f"Deterministic mapping based on failure_reason: '{request.failure_reason}'",
                f"Contextual modifiers applied: attempts={request.previous_attempts}, history='{request.customer_history}'",
                f"Matched category: {cat}"
            ],
            recovery_probabilities=probs,
            reasoning_summary="Mock deterministic evaluation applied successfully with context modifiers."
        )


class GeminiDiagnosisProvider(DiagnosisProvider):
    """
    Integrates with Google Gemini via REST API to provide AI diagnoses.
    Strictly uses response schemas to enforce DiagnosisResponse shape.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.timeout_seconds = 5.0
        
    def get_diagnosis(self, request: DiagnosisRequest) -> DiagnosisResponse:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        
        # Build prompt
        prompt = (
            "You are a Payment Intelligence AI for RecoverOS.\n"
            "Analyze this payment failure and output a strict JSON diagnosis.\n"
            f"Failure Reason: {request.failure_reason}\n"
            f"Amount: {request.payment_amount}\n"
            f"Method: {request.payment_method}\n"
            f"Customer History: {request.customer_history}\n"
            f"Previous Attempts: {request.previous_attempts}\n"
            f"Days Overdue: {request.days_overdue}\n"
        )
        
        # Gemini 1.5 JSON schema format
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "failure_category": {
                            "type": "string",
                            "enum": ["insufficient_funds", "bank_timeout", "hard_decline", "fraud_suspected", "expired_card", "unknown"]
                        },
                        "confidence": {"type": "number"},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "recovery_probabilities": {
                            "type": "object",
                            "properties": {
                                "payment_link": {"type": "number"},
                                "retry": {"type": "number"},
                                "escalate": {"type": "number"},
                                "no_action": {"type": "number"}
                            },
                            "required": ["payment_link", "retry", "escalate", "no_action"]
                        },
                        "reasoning_summary": {"type": "string"}
                    },
                    "required": ["failure_category", "confidence", "evidence", "recovery_probabilities", "reasoning_summary"]
                }
            }
        }
        
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                
                text_response = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed_json = json.loads(text_response)
                
                # Pydantic validation
                return DiagnosisResponse(**parsed_json)
                
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f"Gemini API request failed: {e}")
            raise RuntimeError(f"Gemini API error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Gemini returned invalid JSON: {e}")
            raise RuntimeError(f"JSON parsing error: {e}")
        except KeyError as e:
            logger.error(f"Unexpected Gemini response structure: {e}")
            raise RuntimeError(f"Malformed response structure: {e}")
