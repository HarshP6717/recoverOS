"""
RecoverOS Dashboard Repository.

Read-only data access layer for the dashboard, using SQLAlchemy for aggregation.
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.app.core.config import RAZORPAY_LIVE_EXECUTION
from backend.app.models.database import (
    ActionExecutionModel,
    RecoveryJourneyModel,
    RecoveryEventModel,
)
from backend.app.schemas.dashboard import (
    DashboardOverviewResponse,
    JourneySummary,
    JourneyListResponse,
    JourneyDetailResponse,
    TimelineEvent,
    JourneyTimelineResponse,
)
from backend.app.schemas.recovery import CounterfactualData, ActionCandidateEvaluation


def get_overview_metrics(db: Session) -> DashboardOverviewResponse:
    """Aggregates high-level metrics for the dashboard overview."""
    
    revenue_at_risk = db.query(func.sum(RecoveryJourneyModel.amount)).scalar() or 0.0
    recovered_amount = db.query(func.sum(RecoveryJourneyModel.recovered_amount)).scalar() or 0.0
    recovery_cost = db.query(func.sum(RecoveryJourneyModel.cumulative_cost)).scalar() or 0.0
    net_recovered_value = db.query(func.sum(RecoveryJourneyModel.net_value)).scalar() or 0.0
    
    active_journeys = db.query(func.count(RecoveryJourneyModel.id)).filter(RecoveryJourneyModel.status == "IN_PROGRESS").scalar() or 0
    recovered_journeys = db.query(func.count(RecoveryJourneyModel.id)).filter(RecoveryJourneyModel.status == "RECOVERED").scalar() or 0
    escalated_journeys = db.query(func.count(RecoveryJourneyModel.id)).filter(RecoveryJourneyModel.status == "ESCALATED").scalar() or 0
    exhausted_journeys = db.query(func.count(RecoveryJourneyModel.id)).filter(RecoveryJourneyModel.status == "EXHAUSTED").scalar() or 0
    
    execution_unknown_count = db.query(func.count(ActionExecutionModel.id)).filter(ActionExecutionModel.execution_status == "EXECUTION_UNKNOWN").scalar() or 0

    recovery_rate = (recovered_amount / revenue_at_risk) if revenue_at_risk > 0 else 0.0

    return DashboardOverviewResponse(
        revenue_at_risk=revenue_at_risk,
        recovered_amount=recovered_amount,
        recovery_rate=recovery_rate,
        recovery_cost=recovery_cost,
        friction_cost=None,  # Not persisted in DB
        net_recovered_value=net_recovered_value,
        active_journeys=active_journeys,
        recovered_journeys=recovered_journeys,
        escalated_journeys=escalated_journeys,
        exhausted_journeys=exhausted_journeys,
        cancellation_pending_count=None,  # Not persisted in DB
        execution_unknown_count=execution_unknown_count,
    )


def get_journeys(
    db: Session,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> JourneyListResponse:
    """Returns a paginated, filterable list of journey summaries."""
    
    query = db.query(RecoveryJourneyModel)
    
    if status:
        query = query.filter(RecoveryJourneyModel.status == status.upper())
        
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                RecoveryJourneyModel.transaction_id.ilike(search_term),
                RecoveryJourneyModel.customer_id.ilike(search_term),
                RecoveryJourneyModel.journey_id.ilike(search_term)
            )
        )
        
    total = query.count()
    
    # Bounded pagination max 100
    safe_limit = min(limit, 100)
    
    journeys = query.order_by(RecoveryJourneyModel.created_at.desc()).offset(offset).limit(safe_limit).all()
    
    items = []
    for j in journeys:
        items.append(
            JourneySummary(
                journey_id=j.journey_id,
                transaction_id=j.transaction_id,
                status=j.status,
                current_round=j.current_round,
                original_amount=j.amount,
                recovered_amount=j.recovered_amount,
                cumulative_cost=j.cumulative_cost,
                net_value=j.net_value,
                active_payment_link_id=j.active_payment_link_id,
                created_at=j.created_at,
                updated_at=j.updated_at
            )
        )
        
    return JourneyListResponse(
        total=total,
        limit=safe_limit,
        offset=offset,
        items=items
    )


def get_journey_detail(db: Session, journey_id: str) -> Optional[JourneyDetailResponse]:
    """Retrieves a complete investigation object for a journey."""
    
    journey = db.query(RecoveryJourneyModel).filter(RecoveryJourneyModel.journey_id == journey_id).first()
    if not journey:
        return None
        
    # Find latest recovery event for context
    latest_event = (
        db.query(RecoveryEventModel)
        .filter(RecoveryEventModel.transaction_id == journey.transaction_id)
        .order_by(RecoveryEventModel.created_at.desc())
        .first()
    )
    
    # Find latest action execution
    latest_execution = (
        db.query(ActionExecutionModel)
        .filter(ActionExecutionModel.transaction_id == journey.transaction_id)
        .order_by(ActionExecutionModel.created_at.desc())
        .first()
    )
    
    response = JourneyDetailResponse(
        journey_id=journey.journey_id,
        transaction_id=journey.transaction_id,
        customer_id=journey.customer_id,
        subscription_id=journey.subscription_id,
        amount=journey.amount,
        payment_method=journey.payment_method,
        failure_type=journey.failure_type,
        current_round=journey.current_round,
        status=journey.status,
        termination_reason=journey.termination_reason,
        active_action=journey.active_action,
        active_payment_link_id=journey.active_payment_link_id,
        active_payment_link_url=journey.active_payment_link_url,
        cumulative_cost=journey.cumulative_cost,
        recovered_amount=journey.recovered_amount,
        net_value=journey.net_value,
        contact_count=journey.contact_count,
        days_overdue=journey.days_overdue,
        created_at=journey.created_at,
        updated_at=journey.updated_at,
    )
    
    if latest_event:
        response.latest_diagnosis_status = latest_event.decision_status
        response.selected_action = latest_event.selected_action
        
        cf_dict = latest_event.get_counterfactual_dict()
        if cf_dict:
            response.counterfactual = CounterfactualData(**cf_dict)
            
        response.guardrails_triggered = latest_event.get_guardrails_list()
        raw_candidates = latest_event.get_candidates_list()
        response.candidate_evaluations = [
            ActionCandidateEvaluation(**c) if isinstance(c, dict) else c
            for c in raw_candidates
        ]
        
    if latest_execution:
        response.latest_execution_status = latest_execution.execution_status
        # cancellation_pending is not persisted
        response.cancellation_pending = None
        
    return response


def get_journey_timeline(db: Session, journey_id: str) -> Optional[JourneyTimelineResponse]:
    """Generates a chronological audit timeline for a journey."""
    
    journey = db.query(RecoveryJourneyModel).filter(RecoveryJourneyModel.journey_id == journey_id).first()
    if not journey:
        return None
        
    events = []
    
    # 1. Recovery Events
    recovery_events = (
        db.query(RecoveryEventModel)
        .filter(RecoveryEventModel.transaction_id == journey.transaction_id)
        .all()
    )
    for ev in recovery_events:
        events.append(
            TimelineEvent(
                timestamp=ev.created_at,
                event_type="recovery_decision",
                source=ev.source,
                status=ev.decision_status,
                summary=f"Decision: {ev.selected_action} ({ev.decision_reason})",
                financial_value=ev.amount,
                correlation_id=ev.event_id,
                is_live=False
            )
        )
        
    # 2. Action Executions
    action_executions = (
        db.query(ActionExecutionModel)
        .filter(ActionExecutionModel.transaction_id == journey.transaction_id)
        .all()
    )
    for ex in action_executions:
        # We consider payment link creation as 'LIVE' interaction in this system
        is_live = ex.selected_action in {"recovery_link", "cancel_link"}
        
        events.append(
            TimelineEvent(
                timestamp=ex.execution_timestamp or ex.created_at,
                event_type="action_execution",
                source="action_executor",
                status=ex.execution_status,
                summary=f"Executed Action: {ex.selected_action}",
                financial_value=None,
                correlation_id=ex.execution_id,
                is_live=is_live
            )
        )
        
    # Sort chronological
    events.sort(key=lambda x: x.timestamp)
    
    return JourneyTimelineResponse(
        journey_id=journey.journey_id,
        events=events
    )
