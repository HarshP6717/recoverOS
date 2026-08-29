from typing import Literal, List, Dict
from pydantic import BaseModel, Field, ConfigDict

class DiagnosisRequest(BaseModel):
    """
    Input data provided to the Payment Intelligence Engine for evaluation.
    """
    failure_reason: str
    payment_amount: float
    payment_method: str
    customer_history: str = ""
    previous_attempts: int = 0
    days_overdue: int = 0
    journey_round: int = 1
    transaction_context: Dict[str, str] = Field(default_factory=dict)


class DiagnosisResponse(BaseModel):
    """
    Strictly validated output from the Payment Intelligence Engine.
    LLM output must map to this exact schema.
    """
    failure_category: Literal[
        "insufficient_funds",
        "bank_timeout",
        "hard_decline",
        "fraud_suspected",
        "expired_card",
        "unknown"
    ] = Field(..., description="Categorization of the failure.")
    
    confidence: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Engine's confidence in this diagnosis (0.0 to 1.0)."
    )
    
    evidence: List[str] = Field(
        ..., 
        description="List of strings explaining the evidence used for diagnosis."
    )
    
    recovery_probabilities: Dict[
        Literal["payment_link", "retry", "escalate", "no_action"], 
        float
    ] = Field(
        ..., 
        description="Estimated probability (0.0 to 1.0) of recovery for each action."
    )
    
    reasoning_summary: str = Field(
        ..., 
        description="A human-readable summary of the LLM's reasoning."
    )

    model_config = ConfigDict(extra='forbid')
