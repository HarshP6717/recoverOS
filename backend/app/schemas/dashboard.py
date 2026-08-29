"""
RecoverOS Dashboard API Schemas.

Defines the structure for read-only dashboard overview, journey summaries,
and timeline events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.schemas.recovery import CounterfactualData, ActionCandidateEvaluation


class DashboardOverviewResponse(BaseModel):
    """Overview metrics for the RecoverOS dashboard."""
    revenue_at_risk: float
    recovered_amount: float
    recovery_rate: float
    recovery_cost: float
    friction_cost: Optional[float] = None
    net_recovered_value: float
    active_journeys: int
    recovered_journeys: int
    escalated_journeys: int
    exhausted_journeys: int
    cancellation_pending_count: Optional[int] = None
    execution_unknown_count: int


class JourneySummary(BaseModel):
    """Compact journey summary for lists."""
    journey_id: str
    transaction_id: str
    status: str
    current_round: int
    original_amount: float
    recovered_amount: float
    cumulative_cost: float
    net_value: float
    active_payment_link_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class JourneyListResponse(BaseModel):
    """Paginated list of journeys."""
    total: int
    limit: int
    offset: int
    items: List[JourneySummary]


class JourneyDetailResponse(BaseModel):
    """Complete investigation object for a single journey."""
    journey_id: str
    transaction_id: str
    customer_id: Optional[str]
    subscription_id: Optional[str]
    amount: float
    payment_method: str
    failure_type: str
    current_round: int
    status: str
    termination_reason: Optional[str]
    active_action: Optional[str]
    active_payment_link_id: Optional[str]
    active_payment_link_url: Optional[str]
    cumulative_cost: float
    recovered_amount: float
    net_value: float
    contact_count: int
    days_overdue: float
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    # Diagnostic & Orchestration context
    latest_diagnosis_status: Optional[str] = None
    selected_action: Optional[str] = None
    counterfactual: Optional[CounterfactualData] = None
    guardrails_triggered: List[str] = Field(default_factory=list)
    candidate_evaluations: List[ActionCandidateEvaluation] = Field(default_factory=list)
    
    # Execution & Safety
    latest_execution_status: Optional[str] = None
    cancellation_pending: Optional[bool] = None


class TimelineEvent(BaseModel):
    """A chronological event in a journey's timeline."""
    timestamp: datetime
    event_type: str
    source: str
    status: str
    summary: str
    financial_value: Optional[float] = None
    correlation_id: Optional[str] = None
    is_live: bool = False  # Distinguishes LIVE Razorpay actions vs SIMULATED


class JourneyTimelineResponse(BaseModel):
    """Chronological audit timeline for a journey."""
    journey_id: str
    events: List[TimelineEvent]
