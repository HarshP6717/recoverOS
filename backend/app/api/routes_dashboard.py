"""
RecoverOS Dashboard API Routes.

Read-only endpoints for the operational dashboard.
Strictly restricted from mutating state or invoking external integrations.
"""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import get_db
from backend.app.repositories.dashboard_repository import (
    get_overview_metrics,
    get_journeys,
    get_journey_detail,
    get_journey_timeline,
)
from backend.app.schemas.dashboard import (
    DashboardOverviewResponse,
    JourneyListResponse,
    JourneyDetailResponse,
    JourneyTimelineResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dashboard", tags=["Dashboard"])


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get aggregated metrics for the dashboard overview",
)
def get_overview(
    db: Session = Depends(get_db),
) -> DashboardOverviewResponse:
    """Retrieves high-level mathematical aggregations from the ledger."""
    return get_overview_metrics(db)


@router.get(
    "/journeys",
    response_model=JourneyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List recovery journeys with pagination and filtering",
)
def list_journeys(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by exact journey status"),
    search: Optional[str] = Query(None, description="Search by transaction, customer, or journey ID"),
    limit: int = Query(20, ge=1, le=100, description="Pagination limit (max 100)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
) -> JourneyListResponse:
    """Returns a bounded, paginated list of recovery journeys."""
    # Note: limit is bounded at 100 by the query param validator, but repository also bounds it
    return get_journeys(db, status=status_filter, search=search, limit=limit, offset=offset)


@router.get(
    "/journeys/{journey_id}",
    response_model=JourneyDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get comprehensive details for a specific journey",
)
def get_journey(
    journey_id: str,
    db: Session = Depends(get_db),
) -> JourneyDetailResponse:
    """Fetches a complete investigation object encompassing state, decisions, ERV, and execution."""
    detail = get_journey_detail(db, journey_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journey '{journey_id}' not found."
        )
    return detail


@router.get(
    "/journeys/{journey_id}/timeline",
    response_model=JourneyTimelineResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a chronological audit timeline for a journey",
)
def get_timeline(
    journey_id: str,
    db: Session = Depends(get_db),
) -> JourneyTimelineResponse:
    """Fetches chronological events related to a specific journey."""
    timeline = get_journey_timeline(db, journey_id)
    if not timeline:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journey '{journey_id}' not found."
        )
    return timeline
