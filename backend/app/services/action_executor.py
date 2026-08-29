"""
RecoverOS Action Execution Simulator.

Simulates executing recovery actions safely in test/sandbox mode without moving real money:
- retry_now: Simulates immediate gateway re-authorization attempt.
- retry_later: Simulates scheduling a delayed dunning queue job.
- send_reminder: Simulates automated customer notification (WhatsApp/SMS/Email).
- payment_method_update: Simulates generating secure customer card/mandate update session.
- recovery_link: Simulates creating a hosted Razorpay checkout recovery link.
- escalate_human: Simulates opening a high-priority CS/Account Management recovery ticket.
- stop: Formally ceases dunning and marks recovery workflow terminated.

Deterministic seeds based on transaction_id / event_id guarantee exact test reproducibility.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from backend.app.schemas.recovery import DecisionResponse, ExecutionResponse
from backend.app.services.razorpay_client import (
    RazorpayAuthenticationError,
    RazorpayClientError,
    RazorpayGatewayUnavailableError,
    RazorpayTestClient,
    RazorpayTimeoutError,
)

logger = logging.getLogger(__name__)


class ActionExecutionSimulator:
    """
    Simulates recovery action execution safely in test-mode.
    """

    def __init__(self, razorpay_client: Optional[RazorpayTestClient] = None):
        self.razorpay_client = razorpay_client or RazorpayTestClient()

    def _get_deterministic_seed(self, seed_str: str) -> int:
        """Derives a deterministic 32-bit integer seed from a string identifier."""
        return int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:8], 16)

    def execute_action(
        self,
        decision: DecisionResponse,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResponse:
        """
        Simulates execution of the selected action for a decision event.

        Parameters
        ----------
        decision : DecisionResponse
            Orchestrated decision response containing event_id, transaction_id, selected_action, amount.
        request_context : Optional[Dict[str, Any]]
            Additional context metadata.

        Returns
        -------
        ExecutionResponse
            Complete execution record including execution_id, status, simulated response payload,
            and error information (if any).
        """
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)
        action = decision.selected_action
        event_id = decision.event_id
        transaction_id = decision.transaction_id
        amount = decision.amount
        customer_id = decision.customer_id
        subscription_id = decision.subscription_id

        logger.info(f"Executing simulated action '{action}' for event {event_id} (tx: {transaction_id})")

        try:
            if action == "stop":
                return self._simulate_stop(execution_id, event_id, transaction_id, now)

            elif action == "retry_now":
                return self._simulate_retry_now(
                    execution_id, event_id, transaction_id, amount, customer_id, now
                )

            elif action == "retry_later":
                return self._simulate_retry_later(
                    execution_id, event_id, transaction_id, amount, customer_id, now
                )

            elif action == "send_reminder":
                return self._simulate_send_reminder(
                    execution_id, event_id, transaction_id, amount, customer_id, now
                )

            elif action == "payment_method_update":
                return self._simulate_payment_method_update(
                    execution_id, event_id, transaction_id, customer_id, subscription_id, now
                )

            elif action == "recovery_link":
                return self._simulate_recovery_link(
                    execution_id, event_id, transaction_id, amount, customer_id, now
                )

            elif action == "escalate_human":
                return self._simulate_escalate_human(
                    execution_id, event_id, transaction_id, amount, customer_id, now
                )

            else:
                return ExecutionResponse(
                    execution_id=execution_id,
                    event_id=event_id,
                    transaction_id=transaction_id,
                    selected_action=action,
                    execution_status="EXECUTION_FAILED",
                    execution_timestamp=now,
                    simulated_response={},
                    error_code="UNSUPPORTED_ACTION",
                    error_message=f"Action '{action}' is not supported by the execution engine.",
                )

        except RazorpayAuthenticationError as e:
            logger.error(f"Razorpay authentication failure during execution {execution_id}: {e}")
            return ExecutionResponse(
                execution_id=execution_id,
                event_id=event_id,
                transaction_id=transaction_id,
                selected_action=action,
                execution_status="EXECUTION_FAILED",
                execution_timestamp=now,
                simulated_response={"gateway": "razorpay_test_mode", "error_type": "AUTHENTICATION_ERROR"},
                error_code="INVALID_CREDENTIALS",
                error_message=str(e),
            )

        except RazorpayTimeoutError as e:
            logger.error(f"Razorpay timeout during execution {execution_id}: {e}")
            return ExecutionResponse(
                execution_id=execution_id,
                event_id=event_id,
                transaction_id=transaction_id,
                selected_action=action,
                execution_status="EXECUTION_UNKNOWN",
                execution_timestamp=now,
                simulated_response={"gateway": "razorpay_test_mode", "error_type": "GATEWAY_TIMEOUT"},
                error_code="GATEWAY_TIMEOUT",
                error_message=str(e),
            )

        except RazorpayGatewayUnavailableError as e:
            logger.error(f"Razorpay gateway unavailable during execution {execution_id}: {e}")
            return ExecutionResponse(
                execution_id=execution_id,
                event_id=event_id,
                transaction_id=transaction_id,
                selected_action=action,
                execution_status="EXECUTION_FAILED",
                execution_timestamp=now,
                simulated_response={"gateway": "razorpay_test_mode", "error_type": "GATEWAY_UNAVAILABLE"},
                error_code="GATEWAY_UNAVAILABLE",
                error_message=str(e),
            )

        except Exception as e:
            logger.error(f"Unexpected execution failure for event {event_id}: {e}")
            return ExecutionResponse(
                execution_id=execution_id,
                event_id=event_id,
                transaction_id=transaction_id,
                selected_action=action,
                execution_status="EXECUTION_FAILED",
                execution_timestamp=now,
                simulated_response={"error_type": "INTERNAL_EXECUTION_ERROR"},
                error_code="EXECUTION_ERROR",
                error_message=str(e),
            )

    def _simulate_stop(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="stop",
            execution_status="STOPPED",
            execution_timestamp=now,
            simulated_response={
                "action": "stop",
                "dunning_halted": True,
                "recovery_status": "terminated",
                "message": "Dunning sequence ceased per policy decision. No further customer contacts or retries.",
            },
        )

    def _simulate_retry_now(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        amount: float,
        customer_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        # Check test client fault injection
        self.razorpay_client._check_fault_injection()

        seed = self._get_deterministic_seed(f"{transaction_id}_{event_id}_retry")
        # In test mode, deterministic pseudo-outcome (e.g. 70% success on retry_now simulation)
        is_success = (seed % 100) < 70

        auth_code = f"AUTH_{seed % 1000000:06d}"
        status = "SIMULATED_RECOVERED" if is_success else "SIMULATED_FAILED"

        sim_response = {
            "gateway": "razorpay_test_mode",
            "operation": "payment_reauthorization",
            "payment_id": f"pay_retry_{seed % 999999:06d}",
            "amount_inr": amount,
            "currency": "INR",
            "authorization_code": auth_code if is_success else None,
            "simulated_outcome": "authorized" if is_success else "bank_declined",
            "test_mode": True,
        }

        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="retry_now",
            execution_status=status,
            execution_timestamp=now,
            simulated_response=sim_response,
        )

    def _simulate_retry_later(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        amount: float,
        customer_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        self.razorpay_client._check_fault_injection()

        scheduled_time = now + timedelta(hours=48)
        job_id = f"job_dunning_{uuid.uuid4().hex[:12]}"

        sim_response = {
            "queue": "delayed_dunning_queue",
            "job_id": job_id,
            "scheduled_for": scheduled_time.isoformat(),
            "retry_delay_hours": 48,
            "amount_inr": amount,
            "status": "queued",
        }

        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="retry_later",
            execution_status="SIMULATED_PENDING",
            execution_timestamp=now,
            simulated_response=sim_response,
        )

    def _simulate_send_reminder(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        amount: float,
        customer_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        self.razorpay_client._check_fault_injection()

        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        sim_response = {
            "channel": "whatsapp_and_email",
            "message_id": message_id,
            "recipient_customer_id": customer_id,
            "template": "subscription_payment_failed_reminder",
            "amount_inr": amount,
            "delivery_status": "delivered_simulated",
        }

        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="send_reminder",
            execution_status="SUCCESS",
            execution_timestamp=now,
            simulated_response=sim_response,
        )

    def _simulate_payment_method_update(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        customer_id: str,
        subscription_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        session_info = self.razorpay_client.create_customer_update_session(
            customer_id=customer_id,
            subscription_id=subscription_id,
        )
        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="payment_method_update",
            execution_status="SUCCESS",
            execution_timestamp=now,
            simulated_response=session_info,
        )

    def _simulate_recovery_link(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        amount: float,
        customer_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        link_info = self.razorpay_client.create_payment_link(
            amount=amount,
            customer_id=customer_id,
            description=f"Subscription Recovery Payment (Invoice: {transaction_id})",
            reference_id=event_id,
            notes={"event_id": event_id, "transaction_id": transaction_id},
        )
        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="recovery_link",
            execution_status="SUCCESS",
            execution_timestamp=now,
            simulated_response=link_info,
        )

    def _simulate_escalate_human(
        self,
        execution_id: str,
        event_id: str,
        transaction_id: str,
        amount: float,
        customer_id: str,
        now: datetime,
    ) -> ExecutionResponse:
        self.razorpay_client._check_fault_injection()

        ticket_id = f"tkt_rec_{uuid.uuid4().hex[:8]}"
        sim_response = {
            "crm": "concierge_recovery_queue",
            "ticket_id": ticket_id,
            "priority": "P1_URGENT" if amount >= 2000 else "P2_HIGH",
            "assigned_team": "high_touch_revenue_retention",
            "customer_id": customer_id,
            "invoice_amount_inr": amount,
            "action_required": "Personal outreach & concierge payment collection",
        }
        return ExecutionResponse(
            execution_id=execution_id,
            event_id=event_id,
            transaction_id=transaction_id,
            selected_action="escalate_human",
            execution_status="SUCCESS",
            execution_timestamp=now,
            simulated_response=sim_response,
        )
