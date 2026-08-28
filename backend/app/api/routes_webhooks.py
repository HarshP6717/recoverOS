"""
RecoverOS Webhook Ingestion API Endpoints.

Implements:
- POST /v1/webhooks/razorpay: HMAC-SHA256 signature verification, atomic idempotency,
  event routing (Failure Events -> RecoveryOrchestrator, Settlement Events -> ReconciliationService).
"""

from __future__ import annotations

import hashlib
import json
import logging
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import (
    get_db,
    get_razorpay_adapter,
    get_reconciliation_service,
    get_recovery_orchestrator,
)
from backend.app.repositories.event_repository import (
    attach_recovery_event_to_webhook,
    reserve_webhook_event_atomic,
)
from backend.app.schemas.recovery import WebhookProcessingResponse
from backend.app.services.razorpay_adapter import RazorpayAdapter
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.recovery_orchestrator import RecoveryOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks Ingestion"])


@router.post(
    "/razorpay",
    response_model=WebhookProcessingResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest and process Razorpay payment failure and settlement webhooks",
    description="Verifies HMAC-SHA256 signature, ensures atomic idempotency via database unique constraint, routes failures to RecoveryOrchestrator and settlements to ReconciliationService.",
)
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str = Header(None, alias="X-Razorpay-Event-Id"),
    db: Session = Depends(get_db),
    razorpay_adapter: RazorpayAdapter = Depends(get_razorpay_adapter),
    recovery_orchestrator: RecoveryOrchestrator = Depends(get_recovery_orchestrator),
    reconciliation_service: ReconciliationService = Depends(get_reconciliation_service),
) -> WebhookProcessingResponse:
    # 1. Read raw byte payload for HMAC signature verification
    raw_body = await request.body()

    # 2. Verify HMAC-SHA256 signature
    if not razorpay_adapter.verify_signature(raw_body, x_razorpay_signature):
        logger.warning("Rejected webhook: Invalid or missing X-Razorpay-Signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Razorpay-Signature header.",
        )

    # 3. Decode JSON payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        logger.error(f"Malformed JSON in webhook body: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body.",
        )

    event_type = payload.get("event", "unknown")
    webhook_event_id = (
        x_razorpay_event_id
        or payload.get("event_id")
        or payload.get("id")
        or f"rzp_evt_{hashlib.sha256(raw_body).hexdigest()[:16]}"
    )

    # 4. Filter unsupported events gracefully
    if not razorpay_adapter.is_supported_event(event_type):
        logger.info(f"Ignoring unsupported event: {event_type}")
        return WebhookProcessingResponse(
            status="ignored_unsupported_event",
            webhook_event_id=str(webhook_event_id),
            event_type=event_type,
            decision=None,
            message=f"Event type '{event_type}' is not a supported recovery or settlement event.",
        )

    # 5. Atomic Webhook Idempotency Check
    # Attempts atomic reservation using DB UNIQUE constraint on webhook_event_id
    is_new_event = reserve_webhook_event_atomic(db, str(webhook_event_id), event_type)
    if not is_new_event:
        logger.info(f"Duplicate webhook delivery ignored for {webhook_event_id}")
        return WebhookProcessingResponse(
            status="duplicate_ignored",
            webhook_event_id=str(webhook_event_id),
            event_type=event_type,
            decision=None,
            message="Duplicate webhook event delivery ignored via atomic idempotency constraint.",
        )

    # 6. Branch A: Closed-Loop Settlement Events (payment_link.paid, payment.captured, subscription.charged)
    if razorpay_adapter.is_settlement_event(event_type):
        recon_result = reconciliation_service.reconcile_settlement(
            db=db,
            event_type=event_type,
            payload=payload,
            webhook_event_id=str(webhook_event_id),
        )
        return WebhookProcessingResponse(
            status="processed" if recon_result.status == "reconciled" else recon_result.status,
            event_id=str(webhook_event_id),
            webhook_event_id=str(webhook_event_id),
            event_type=event_type,
            decision=None,
            execution=None,
            message=recon_result.message,
        )

    # 7. Branch B: Inbound Failure Events (payment.failed, subscription.halted, etc.)
    try:
        _, decision_request = razorpay_adapter.normalize_webhook_payload(
            payload, webhook_event_id=str(webhook_event_id)
        )
    except Exception as e:
        logger.error(f"Payload normalization failed for webhook {webhook_event_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Webhook payload normalization error: {str(e)}",
        )

    # Orchestrate recovery decision and update journey state
    orch_result = recovery_orchestrator.process_recovery(
        db=db,
        request=decision_request,
        raw_payload=payload,
        auto_execute=True,
    )

    # Link recovery event ID to webhook reservation record
    if orch_result.decision:
        attach_recovery_event_to_webhook(db, str(webhook_event_id), orch_result.decision.event_id)

    return WebhookProcessingResponse(
        status="processed",
        event_id=orch_result.decision.event_id if orch_result.decision else str(webhook_event_id),
        webhook_event_id=str(webhook_event_id),
        event_type=event_type,
        decision=orch_result.decision,
        execution=orch_result.execution,
        message="Webhook processed, journey updated, decision audited, and recovery action executed successfully.",
    )
