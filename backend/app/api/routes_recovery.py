"""
RecoverOS Recovery API Endpoints.

Implements:
- POST /v1/recover/diagnose: Dry-run ML diagnosis & candidate ERV guardrail audit.
- POST /v1/recover/decision: Full decision orchestration with SQLite ledger persistence.
- GET /v1/recover/events/{event_id}: Audit record retrieval.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import (
    get_db,
    get_decision_engine,
    get_event_service,
)
from backend.app.schemas.recovery import (
    ActionExecutionAuditRecord,
    DecisionRequest,
    DecisionResponse,
    DiagnosisRequest,
    DiagnosisResponse,
    ExecutionRequest,
    ExecutionResponse,
    RecoveryEventAuditRecord,
)
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/recover", tags=["Recovery Control Plane"])


@router.post(
    "/diagnose",
    response_model=DiagnosisResponse,
    summary="Diagnose payment failure and evaluate candidate actions (Dry-Run)",
    description="Evaluates all 7 candidate recovery actions, computes predicted ERVs in INR (₹), and audits guardrails without ledger persistence.",
)
def diagnose_payment_failure(
    request: DiagnosisRequest,
    decision_engine: DecisionEngine = Depends(get_decision_engine),
) -> DiagnosisResponse:
    try:
        return decision_engine.diagnose(request)
    except Exception as e:
        logger.error(f"Diagnosis endpoint error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Diagnosis evaluation failed: {str(e)}",
        )


@router.post(
    "/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Orchestrate recovery action decision, persist audit ledger, and execute simulation",
    description="Executes candidate scoring, ERV calculation, guardrail selection, atomic persistence to SQLite, and simulated action execution.",
)
def orchestrate_recovery_decision(
    request: DecisionRequest,
    auto_execute: bool = True,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> DecisionResponse:
    try:
        return event_service.process_decision(db, request, auto_execute=auto_execute)
    except RuntimeError as e:
        logger.error(f"Ledger persistence failure during decision orchestration: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Safe Failure: Decision could not be audited to the event ledger. Automated actions halted. Error: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Decision endpoint error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Decision orchestration failed: {str(e)}",
        )


@router.post(
    "/execute",
    response_model=ExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger recovery action execution simulation for an existing event ID",
    description="Executes the selected recovery action in test-mode simulation and records the execution result to the audit ledger.",
)
def execute_recovery_action(
    request: ExecutionRequest,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> ExecutionResponse:
    try:
        return event_service.execute_event(db, request.event_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Action execution error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Action execution simulation failed: {str(e)}",
        )


@router.get(
    "/events/{event_id}",
    response_model=RecoveryEventAuditRecord,
    summary="Retrieve audit record by event ID",
    description="Fetches full audit trail details including candidate evaluations, guardrails, decision metadata, and execution history.",
)
def get_recovery_event(
    event_id: str,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> RecoveryEventAuditRecord:
    event_record = event_service.get_event(db, event_id)
    if not event_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recovery audit event '{event_id}' not found.",
        )
    return event_record


@router.get(
    "/executions/{execution_id}",
    response_model=ActionExecutionAuditRecord,
    summary="Retrieve action execution record by execution ID",
    description="Fetches full execution status, timestamp, simulated response payload, and error information.",
)
def get_action_execution(
    execution_id: str,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> ActionExecutionAuditRecord:
    exec_record = event_service.get_execution(db, execution_id)
    if not exec_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action execution record '{execution_id}' not found.",
        )
    return exec_record
