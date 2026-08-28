"""
RecoverOS FastAPI Dependency Injection Providers.
"""

from __future__ import annotations

from typing import Generator
from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.models.database import get_db_session
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.razorpay_adapter import RazorpayAdapter
from backend.app.services.razorpay_client import RazorpayTestClient

# Global singleton instances
_guardrail_engine = GuardrailEngine()
_decision_engine = DecisionEngine(guardrail_engine=_guardrail_engine)
_razorpay_client = RazorpayTestClient()
_action_executor = ActionExecutionSimulator(razorpay_client=_razorpay_client)
_event_service = EventService(
    decision_engine=_decision_engine,
    action_executor=_action_executor,
)
_razorpay_adapter = RazorpayAdapter()


def get_db() -> Generator[Session, None, None]:
    """Provides a database session from the connection pool."""
    yield from get_db_session()


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


def get_razorpay_adapter() -> RazorpayAdapter:
    """Provides the RazorpayAdapter instance."""
    return _razorpay_adapter
