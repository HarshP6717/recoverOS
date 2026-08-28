"""
Unit and Integration Tests for RecoverOS Recovery Orchestrator.

Verifies:
- End-to-end orchestration connecting DecisionRequest, DecisionEngine, ActionExecutor, and JourneyService
- Single authoritative contact fatigue and cost tracking
- Safe state transitions (RECOVERED, STOPPED, ESCALATED, EXHAUSTED)
- Terminal state immutability (no further action execution once terminal)
- Idempotent repeated processing
- Financial invariants (net_value = recovered - cost)
- Guardrails enforcement preservation
"""

import pytest
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel
from backend.app.schemas.recovery import DecisionRequest
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.journey_service import JourneyService
from backend.app.services.razorpay_client import RazorpayTestClient
from backend.app.services.recovery_orchestrator import RecoveryOrchestrator


@pytest.fixture
def orchestrator():
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails)
    client = RazorpayTestClient()
    executor = ActionExecutionSimulator(razorpay_client=client)
    journey_svc = JourneyService()
    event_svc = EventService(decision_engine=decision_engine, action_executor=executor)

    return RecoveryOrchestrator(
        journey_service=journey_svc,
        event_service=event_svc,
        decision_engine=decision_engine,
        action_executor=executor,
    )


def make_request(
    tx_id: str = "tx_orch_001",
    amount: float = 2499.0,
    failure_type: str = "expired_card",
    payment_method: str = "card",
    attempt_number: int = 1,
    days_overdue: int = 1,
    contact_count: int = 0,
) -> DecisionRequest:
    return DecisionRequest(
        transaction_id=tx_id,
        customer_id="cust_001",
        subscription_id="sub_001",
        amount=amount,
        payment_method=payment_method,
        failure_type=failure_type,
        attempt_number=attempt_number,
        days_overdue=days_overdue,
        previous_payment_count=10,
        previous_success_count=9,
        previous_failure_count=1,
        previous_recovery_count=1,
        customer_lifetime_value=25000.0,
        contact_count=contact_count,
        subscription_age_days=300,
        source="unit_test",
        external_event_id=f"evt_{tx_id}",
    )


class TestOrchestratorEndToEnd:
    def test_new_failure_creates_journey_and_executes(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Incoming failure initializes RecoveryJourney, invokes decision engine, executes action, and updates state."""
        req = make_request(tx_id="tx_new_001", amount=2499.0, failure_type="expired_card")

        result = orchestrator.process_recovery(db_session, req)

        assert result.journey is not None
        assert result.journey.transaction_id == "tx_new_001"
        assert result.journey.amount == 2499.0
        assert result.journey.current_round == 1
        assert result.decision is not None
        assert result.decision.selected_action in ("payment_method_update", "recovery_link")
        assert result.execution is not None
        assert result.journey.active_action == result.decision.selected_action
        assert result.journey.cumulative_cost > 0.0

    def test_existing_journey_reused(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Repeated processing for same transaction reuses existing journey without duplicate DB entries."""
        req = make_request(tx_id="tx_reuse_001", amount=999.0, failure_type="insufficient_funds")

        res1 = orchestrator.process_recovery(db_session, req)
        res2 = orchestrator.process_recovery(db_session, req)

        assert res1.journey.journey_id == res2.journey.journey_id
        count = db_session.query(RecoveryJourneyModel).filter(RecoveryJourneyModel.transaction_id == "tx_reuse_001").count()
        assert count == 1

    def test_customer_facing_action_increments_fatigue_once(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Customer-facing action (e.g. recovery_link / update) increments contact_count exactly once via JourneyService."""
        req = make_request(tx_id="tx_fatigue_001", amount=1500.0, failure_type="expired_card", contact_count=0)

        result = orchestrator.process_recovery(db_session, req)
        assert result.journey.contact_count == 1

    def test_guardrails_remain_authoritative(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Guardrail 2 prohibits retry_now and retry_later on hard failures (expired_card)."""
        req = make_request(tx_id="tx_guard_001", amount=1000.0, failure_type="expired_card")

        result = orchestrator.process_recovery(db_session, req)
        assert result.decision.selected_action not in ("retry_now", "retry_later")
        assert "G2_PERMANENT_FAILURE_RETRY_SUPPRESSION" in result.decision.guardrails_triggered

    def test_stop_action_terminates_journey(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """When decision is STOP (e.g. micro amount, high fatigue), journey transitions to STOPPED."""
        # Force a stop scenario: micro-amount + high fatigue + high attempts
        req = make_request(
            tx_id="tx_stop_001",
            amount=1.0,
            failure_type="repeated_failure",
            attempt_number=5,
            contact_count=8,
        )

        result = orchestrator.process_recovery(db_session, req)
        assert result.decision.selected_action == "stop"
        assert result.journey.status == "STOPPED"
        assert result.journey.termination_reason == "STOP_ACTION"
        assert result.journey.is_terminal is True

    def test_escalation_terminates_journey(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """When decision is escalate_human, journey transitions to ESCALATED."""
        req = make_request(
            tx_id="tx_esc_001",
            amount=10000.0,
            failure_type="repeated_failure",
            attempt_number=1,
            contact_count=0,
        )

        result = orchestrator.process_recovery(db_session, req)
        if result.decision.selected_action == "escalate_human":
            assert result.journey.status == "ESCALATED"
            assert result.journey.termination_reason == "ESCALATE_ACTION"
            assert result.journey.is_terminal is True


class TestTerminalStateProtection:
    def test_terminal_journey_cannot_execute_again(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Once in a terminal state, orchestrator ignores further execution requests without mutating state."""
        req = make_request(tx_id="tx_term_001", amount=1000.0, failure_type="soft_decline")
        res1 = orchestrator.process_recovery(db_session, req)

        # Manually resolve / reconcile journey to RECOVERED
        orchestrator.reconcile_settlement(db_session, "tx_term_001", recovered_amount=1000.0)
        assert res1.journey.status == "RECOVERED"
        cost_before = res1.journey.cumulative_cost
        net_before = res1.journey.net_value

        # Attempt second processing on terminal journey
        res2 = orchestrator.process_recovery(db_session, req)
        assert res2.status == "RECOVERED"
        assert res2.decision is None
        assert res2.execution is None
        assert "terminal state" in res2.message
        assert res2.journey.cumulative_cost == cost_before
        assert res2.journey.net_value == net_before

    def test_failed_action_does_not_falsely_recover(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """A pending or failed action leaves journey IN_PROGRESS with zero recovered_amount."""
        req = make_request(tx_id="tx_pending_001", amount=2000.0, failure_type="insufficient_funds")

        result = orchestrator.process_recovery(db_session, req)
        if result.decision.selected_action == "retry_later":
            assert result.journey.status == "IN_PROGRESS"
            assert result.journey.recovered_amount == 0.0
            assert result.journey.net_value == 0.0 - result.journey.cumulative_cost


class TestMultiRoundProgression:
    def test_round_progression_and_exhaustion(self, db_session: Session, orchestrator: RecoveryOrchestrator):
        """Journey progresses Round 1 -> Round 2 -> Round 3 -> EXHAUSTED."""
        req = make_request(tx_id="tx_multi_001", amount=1200.0, failure_type="bank_timeout")
        res = orchestrator.process_recovery(db_session, req)
        j_id = res.journey.journey_id
        assert res.journey.current_round == 1

        # Advance to Round 2
        j_r2 = orchestrator.advance_journey_round(db_session, j_id)
        assert j_r2.current_round == 2
        assert j_r2.status == "IN_PROGRESS"

        # Advance to Round 3
        j_r3 = orchestrator.advance_journey_round(db_session, j_id)
        assert j_r3.current_round == 3
        assert j_r3.status == "IN_PROGRESS"

        # Advance past Round 3 -> EXHAUSTED
        j_ex = orchestrator.advance_journey_round(db_session, j_id)
        assert j_ex.status == "EXHAUSTED"
        assert j_ex.termination_reason == "MAX_ROUNDS_REACHED"
        assert j_ex.current_round == 3  # Never becomes 4
        assert j_ex.is_terminal is True


class TestClosedLoopReconciliation:
    def test_reconcile_settlement_updates_recovered_and_net_value(
        self, db_session: Session, orchestrator: RecoveryOrchestrator
    ):
        """Settlement reconciliation marks journey RECOVERED and establishes exact net_value."""
        req = make_request(tx_id="tx_recon_001", amount=3500.0, failure_type="expired_card")
        res = orchestrator.process_recovery(db_session, req)

        j_rec = orchestrator.reconcile_settlement(db_session, "tx_recon_001", recovered_amount=3500.0)
        assert j_rec is not None
        assert j_rec.status == "RECOVERED"
        assert j_rec.recovered_amount == 3500.0
        assert j_rec.net_value == 3500.0 - j_rec.cumulative_cost
        assert j_rec.termination_reason == "RECOVERED"
