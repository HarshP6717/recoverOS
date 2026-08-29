"""
RecoverOS Pydantic Validation and Response Schemas.

All financial amounts, action execution costs, and ERVs are represented in INR (₹).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ActionCandidateEvaluation(BaseModel):
    """Evaluation breakdown for a single candidate recovery action."""

    action: str = Field(..., description="Name of the recovery action")
    predicted_recovery_probability: float = Field(
        ..., ge=0.0, le=1.0, description="Predicted recovery probability"
    )
    action_cost: float = Field(..., ge=0.0, description="Synthetic execution cost in INR (₹)")
    predicted_erv: float = Field(
        ..., description="Predicted Expected Recovery Value (amount * P - cost) in INR (₹)"
    )
    allowed: bool = Field(..., description="Whether action is permitted by guardrails")
    suppression_reason: Optional[str] = Field(
        None, description="Reason if action was suppressed by guardrails"
    )


class CounterfactualData(BaseModel):
    """Structured data comparing the selected action to the next best alternative."""
    
    selected_action: str
    selected_erv: float
    selected_probability: float
    counterfactual_action: str
    counterfactual_erv: float
    counterfactual_probability: float
    value_difference: float = Field(..., description="Selected ERV minus Counterfactual ERV")
    guardrails_applied: List[str] = Field(default_factory=list)


class DiagnosisRequest(BaseModel):
    """Input payload for recovery diagnosis (dry-run without ledger persistence)."""

    transaction_id: str = Field(..., min_length=1, description="Unique transaction ID")
    customer_id: str = Field(..., min_length=1, description="Customer identifier")
    subscription_id: str = Field(..., min_length=1, description="Subscription identifier")
    amount: float = Field(..., gt=0.0, description="Invoice amount at risk in INR (₹)")
    payment_method: str = Field(
        ..., description="Payment method: upi, card, netbanking, mandate_nach, wallet"
    )
    failure_type: str = Field(
        ..., description="Failure category: insufficient_funds, bank_timeout, expired_card, etc."
    )
    attempt_number: int = Field(default=1, ge=1, description="Current recovery attempt index")
    days_overdue: int = Field(default=0, ge=0, description="Days elapsed since invoice due date")
    previous_payment_count: int = Field(
        default=0, ge=0, description="Total historical billing cycles"
    )
    previous_success_count: int = Field(
        default=0, ge=0, description="Historical successful payment count"
    )
    previous_failure_count: int = Field(
        default=0, ge=0, description="Historical failed payment count"
    )
    previous_recovery_count: int = Field(
        default=0, ge=0, description="Historical recovered payment count"
    )
    customer_lifetime_value: float = Field(
        default=0.0, ge=0.0, description="Customer lifetime value in INR (₹)"
    )
    contact_count: int = Field(
        default=0, ge=0, description="Number of recovery messages previously sent"
    )
    subscription_age_days: int = Field(
        default=0, ge=0, description="Subscription age in days since inception"
    )

    @field_validator("payment_method")
    @classmethod
    def normalize_payment_method(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("failure_type")
    @classmethod
    def normalize_failure_type(cls, v: str) -> str:
        return v.strip().lower()


class DecisionRequest(DiagnosisRequest):
    """Input payload for full decision orchestration and ledger persistence."""

    source: str = Field(default="api_direct", description="Source of event: api_direct, razorpay_webhook")
    external_event_id: Optional[str] = Field(
        None, description="External event identifier (e.g., Razorpay webhook event ID)"
    )


class DiagnosisResponse(BaseModel):
    """Diagnostic response with action candidate scoring and guardrail audit."""

    transaction_id: str
    amount: float
    recommended_action: str
    decision_status: str
    decision_reason: str
    guardrails_triggered: List[str]
    candidate_evaluations: List[ActionCandidateEvaluation]
    model_version: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionRequest(BaseModel):
    """Payload to trigger recovery action execution simulation."""

    event_id: str = Field(..., description="RecoverOS event ID to execute")


class ExecutionResponse(BaseModel):
    """Response returned from action execution simulator."""

    execution_id: str = Field(..., description="Unique execution ID (exec_...)")
    event_id: str = Field(..., description="Associated recovery event ID")
    transaction_id: str = Field(..., description="Transaction ID")
    selected_action: str = Field(..., description="Executed recovery action")
    execution_status: str = Field(
        ...,
        description="Execution status: SUCCESS, SIMULATED_RECOVERED, SIMULATED_PENDING, STOPPED, DEGRADED_FALLBACK, EXECUTION_FAILED, EXECUTION_UNKNOWN",
    )
    execution_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of execution"
    )
    simulated_response: Dict[str, Any] = Field(
        default_factory=dict, description="Detailed mock response from simulated gateway/channel"
    )
    error_code: Optional[str] = Field(None, description="Error code if execution failed")
    error_message: Optional[str] = Field(None, description="Error message if execution failed")


class DecisionResponse(BaseModel):
    """Response returned when an action decision is orchestrated and audited."""

    event_id: str = Field(..., description="Unique RecoverOS event audit ID (evt_...)")
    transaction_id: str
    customer_id: str
    subscription_id: str
    amount: float
    selected_action: str
    decision_status: str = Field(
        ..., description="Decision status: SUCCESS, FALLBACK_SAFE, SUPPRESSED_STOP, REJECTED"
    )
    decision_reason: str
    guardrails_triggered: List[str]
    candidate_evaluations: List[ActionCandidateEvaluation]
    counterfactuals: Optional[CounterfactualData] = None
    model_version: str
    timestamp: datetime
    audit_persisted: bool = Field(default=True, description="Whether audit ledger record was saved")
    execution: Optional[ExecutionResponse] = Field(
        None, description="Simulated execution result if auto-executed"
    )


class ActionExecutionAuditRecord(BaseModel):
    """Full database audit record schema for action executions."""

    id: int
    execution_id: str
    event_id: str
    transaction_id: str
    selected_action: str
    execution_status: str
    execution_timestamp: datetime
    simulated_response: Dict[str, Any]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class RecoveryEventAuditRecord(BaseModel):
    """Full database audit record schema for recovery events."""

    id: int
    event_id: str
    source: str
    external_event_id: Optional[str] = None
    transaction_id: str
    customer_id: str
    subscription_id: str
    amount: float
    payment_method: str
    failure_type: str
    attempt_number: int
    days_overdue: int
    previous_payment_count: int
    previous_success_count: int
    previous_failure_count: int
    previous_recovery_count: int
    customer_lifetime_value: float
    contact_count: int
    subscription_age_days: int
    selected_action: str
    decision_status: str
    decision_reason: str
    model_version: str
    guardrails_triggered: List[str]
    candidate_evaluations: List[ActionCandidateEvaluation]
    counterfactuals: Optional[CounterfactualData] = None
    raw_payload: Optional[Dict[str, Any]] = None
    created_at: datetime
    executions: List[ActionExecutionAuditRecord] = Field(default_factory=list)


class WebhookProcessingResponse(BaseModel):
    """Response returned from Razorpay webhook endpoint."""

    status: str = Field(
        ..., description="Processing status: processed, duplicate_ignored, ignored_unsupported_event, error"
    )
    event_id: Optional[str] = Field(None, description="RecoverOS event ID if processed")
    webhook_event_id: str = Field(..., description="External webhook event ID")
    event_type: str = Field(..., description="Normalized webhook event type")
    decision: Optional[DecisionResponse] = Field(
        None, description="Decision orchestration result if processed"
    )
    execution: Optional[ExecutionResponse] = Field(
        None, description="Simulated execution result if processed"
    )
    message: str = Field(..., description="Human-readable processing message")
