"""
RecoverOS FastAPI Dependency Injection Providers.
"""

from __future__ import annotations

from typing import Generator
from fastapi import Depends
from sqlalchemy.orm import Session

import logging
from backend.app.core.config import AI_PROVIDER, GEMINI_API_KEY
from backend.app.models.database import get_db_session
from backend.app.providers.llm_provider import (
    DiagnosisProvider,
    GeminiDiagnosisProvider,
    MockDiagnosisProvider,
)
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.diagnosis_engine import DiagnosisEngine
from backend.app.services.event_service import EventService
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.journey_service import JourneyService
from backend.app.services.razorpay_adapter import RazorpayAdapter
from backend.app.services.razorpay_client import RazorpayTestClient
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.recovery_orchestrator import RecoveryOrchestrator

logger = logging.getLogger(__name__)


def create_diagnosis_provider() -> DiagnosisProvider:
    """Instantiates the appropriate DiagnosisProvider based on AI_PROVIDER config."""
    if AI_PROVIDER == "gemini":
        if GEMINI_API_KEY:
            logger.info("AI Provider configured: GeminiDiagnosisProvider")
            return GeminiDiagnosisProvider(api_key=GEMINI_API_KEY)
        else:
            logger.warning(
                "AI_PROVIDER is set to 'gemini' but GEMINI_API_KEY is missing. Falling back to MockDiagnosisProvider."
            )
    logger.info("AI Provider configured: MockDiagnosisProvider (deterministic mock)")
    return MockDiagnosisProvider()


# Global singleton instances
_diagnosis_provider = create_diagnosis_provider()
_diagnosis_engine = DiagnosisEngine(provider=_diagnosis_provider)
_guardrail_engine = GuardrailEngine()
_decision_engine = DecisionEngine(
    guardrail_engine=_guardrail_engine,
    diagnosis_engine=_diagnosis_engine,
)
_razorpay_client = RazorpayTestClient()
_action_executor = ActionExecutionSimulator(razorpay_client=_razorpay_client)
_event_service = EventService(
    decision_engine=_decision_engine,
    action_executor=_action_executor,
)
_journey_service = JourneyService()
_recovery_orchestrator = RecoveryOrchestrator(
    journey_service=_journey_service,
    event_service=_event_service,
    decision_engine=_decision_engine,
    action_executor=_action_executor,
)
_reconciliation_service = ReconciliationService(
    journey_service=_journey_service,
    razorpay_client=_razorpay_client,
)
_razorpay_adapter = RazorpayAdapter()


def get_db() -> Generator[Session, None, None]:
    """Provides a database session from the connection pool."""
    yield from get_db_session()


def get_diagnosis_engine() -> DiagnosisEngine:
    """Provides the DiagnosisEngine instance."""
    return _diagnosis_engine


def get_decision_engine() -> DecisionEngine:
    """Provides the DecisionEngine instance."""
    return _decision_engine


def get_razorpay_client() -> RazorpayTestClient:
    """Provides the RazorpayTestClient instance."""
    return _razorpay_client


def get_action_executor(
    razorpay_client: RazorpayTestClient = Depends(get_razorpay_client),
) -> ActionExecutionSimulator:
    """Provides the ActionExecutionSimulator instance."""
    return ActionExecutionSimulator(razorpay_client=razorpay_client)


def get_event_service(
    decision_engine: DecisionEngine = Depends(get_decision_engine),
    action_executor: ActionExecutionSimulator = Depends(get_action_executor),
) -> EventService:
    """Provides the EventService instance using injected DecisionEngine and ActionExecutor."""
    return EventService(decision_engine=decision_engine, action_executor=action_executor)


def get_journey_service() -> JourneyService:
    """Provides the JourneyService instance."""
    return _journey_service


def get_recovery_orchestrator(
    journey_service: JourneyService = Depends(get_journey_service),
    event_service: EventService = Depends(get_event_service),
    decision_engine: DecisionEngine = Depends(get_decision_engine),
    action_executor: ActionExecutionSimulator = Depends(get_action_executor),
) -> RecoveryOrchestrator:
    """Provides the RecoveryOrchestrator instance."""
    return RecoveryOrchestrator(
        journey_service=journey_service,
        event_service=event_service,
        decision_engine=decision_engine,
        action_executor=action_executor,
    )


def get_reconciliation_service(
    journey_service: JourneyService = Depends(get_journey_service),
    razorpay_client: RazorpayTestClient = Depends(get_razorpay_client),
) -> ReconciliationService:
    """Provides the ReconciliationService instance."""
    return ReconciliationService(
        journey_service=journey_service,
        razorpay_client=razorpay_client,
    )


def get_razorpay_adapter() -> RazorpayAdapter:
    """Provides the RazorpayAdapter instance."""
    return _razorpay_adapter
