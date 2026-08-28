"""
RecoverOS Phase 2C Step 4 — Sequential Case State Model.

Represents the mutable state of a failed payment case as it progresses
through a multi-round sequential recovery journey (up to 3 rounds).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import pandas as pd


@dataclass
class SequentialCaseState:
    """
    State tracking object for a single payment failure case during sequential dunning.
    """
    case_index: int
    transaction_id: str
    customer_id: str
    subscription_id: str
    amount: float
    payment_method: str
    failure_type: str
    attempt_number: int
    days_overdue: float
    previous_payment_count: int
    previous_success_count: int
    previous_failure_count: int
    previous_recovery_count: int
    customer_lifetime_value: float
    contact_count: int
    subscription_age_days: int

    # Derived rates
    previous_success_rate: float = 0.5
    previous_recovery_rate: float = 0.5

    # Sequential journey progression
    current_round: int = 1
    is_terminated: bool = False
    is_recovered: bool = False
    termination_reason: str = "IN_PROGRESS"
    recovered_round: Optional[int] = None

    # Financial & action accumulators
    cumulative_action_cost: float = 0.0
    cumulative_recovered_amount: float = 0.0
    cumulative_net_value: float = 0.0
    action_history: List[str] = field(default_factory=list)
    round_outcomes: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: pd.Series | Dict[str, Any], index: int) -> SequentialCaseState:
        """Instantiate a fresh state object from an initial test.csv row."""
        prev_pay = max(1, int(row.get("previous_payment_count", 1)))
        prev_succ = int(row.get("previous_success_count", 0))
        prev_fail = max(0, int(row.get("previous_failure_count", 0)))
        prev_rec = int(row.get("previous_recovery_count", 0))

        succ_rate = row.get("previous_success_rate")
        if succ_rate is None or pd.isna(succ_rate):
            succ_rate = prev_succ / prev_pay
        else:
            succ_rate = float(succ_rate)

        rec_rate = row.get("previous_recovery_rate")
        if rec_rate is None or pd.isna(rec_rate):
            rec_rate = (prev_rec / prev_fail) if prev_fail > 0 else 0.5
        else:
            rec_rate = float(rec_rate)

        return cls(
            case_index=index,
            transaction_id=str(row.get("transaction_id", f"tx_{index:05d}")),
            customer_id=str(row.get("customer_id", f"cust_{index:05d}")),
            subscription_id=str(row.get("subscription_id", f"sub_{index:05d}")),
            amount=float(row.get("amount", 0.0)),
            payment_method=str(row.get("payment_method", "card")),
            failure_type=str(row.get("failure_type", "unknown")),
            attempt_number=int(row.get("attempt_number", 1)),
            days_overdue=float(row.get("days_overdue", 0.0)),
            previous_payment_count=prev_pay,
            previous_success_count=prev_succ,
            previous_failure_count=prev_fail,
            previous_recovery_count=prev_rec,
            customer_lifetime_value=float(row.get("customer_lifetime_value", 0.0)),
            contact_count=int(row.get("contact_count", 0)),
            subscription_age_days=int(row.get("subscription_age_days", 30)),
            previous_success_rate=succ_rate,
            previous_recovery_rate=rec_rate,
            current_round=1,
            is_terminated=False,
            is_recovered=False,
            termination_reason="IN_PROGRESS",
            recovered_round=None,
            cumulative_action_cost=0.0,
            cumulative_recovered_amount=0.0,
            cumulative_net_value=0.0,
            action_history=[],
            round_outcomes=[],
        )

    def to_record_dict(self) -> Dict[str, Any]:
        """Convert current state to dictionary format expected by policies and simulator."""
        return {
            "transaction_id": self.transaction_id,
            "customer_id": self.customer_id,
            "subscription_id": self.subscription_id,
            "amount": self.amount,
            "payment_method": self.payment_method,
            "failure_type": self.failure_type,
            "attempt_number": self.attempt_number,
            "days_overdue": self.days_overdue,
            "previous_payment_count": self.previous_payment_count,
            "previous_success_count": self.previous_success_count,
            "previous_failure_count": self.previous_failure_count,
            "previous_recovery_count": self.previous_recovery_count,
            "customer_lifetime_value": self.customer_lifetime_value,
            "contact_count": self.contact_count,
            "subscription_age_days": self.subscription_age_days,
            "previous_success_rate": self.previous_success_rate,
            "previous_recovery_rate": self.previous_recovery_rate,
        }
