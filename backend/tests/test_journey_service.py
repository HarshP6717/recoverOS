"""
Unit and Integration Tests for Stateful Recovery Journey Service.

Verifies:
- Creation and deduplication of recovery journeys
- Round progression invariants (1 -> 2 -> 3 -> EXHAUSTED, no round 4)
- Action recording, cumulative cost tracking, and contact fatigue rules
- Financial invariants (net_value = recovered_amount - cumulative_cost)
- Terminal state transitions and terminal-state immutability
- Idempotency of terminal transitions (no double-counting)
- Rejection of invalid financial inputs
- Session persistence
"""

import pytest
from sqlalchemy.orm import Session

from backend.app.models.database import RecoveryJourneyModel
from backend.app.services.journey_service import JourneyService, MAX_HORIZON_ROUNDS


@pytest.fixture
def journey_service():
    return JourneyService()


class TestJourneyLifecycle:
    def test_create_journey_initial_state(self, db_session: Session, journey_service: JourneyService):
        """New journey initializes in Round 1 with IN_PROGRESS status and zero financial accumulators."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_1001",
            amount=2499.0,
            payment_method="card",
            failure_type="expired_card",
            customer_id="cust_001",
            subscription_id="sub_001",
            days_overdue=2.0,
            contact_count=0,
        )

        assert journey.journey_id.startswith("jrn_")
        assert journey.transaction_id == "tx_test_1001"
        assert journey.amount == 2499.0
        assert journey.current_round == 1
        assert journey.status == "IN_PROGRESS"
        assert journey.termination_reason is None
        assert journey.cumulative_cost == 0.0
        assert journey.recovered_amount == 0.0
        assert journey.net_value == 0.0
        assert journey.contact_count == 0
        assert journey.days_overdue == 2.0
        assert journey.is_terminal is False

    def test_get_existing_journey(self, db_session: Session, journey_service: JourneyService):
        """Retrieving an existing journey returns the exact same record."""
        created = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_1002",
            amount=999.0,
            payment_method="upi",
            failure_type="insufficient_funds",
        )

        fetched = journey_service.get_journey(db_session, created.journey_id)
        assert fetched is not None
        assert fetched.journey_id == created.journey_id
        assert fetched.transaction_id == "tx_test_1002"

    def test_get_or_create_does_not_duplicate(self, db_session: Session, journey_service: JourneyService):
        """Repeated get_or_create with same transaction_id returns the existing journey without duplication."""
        j1 = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_dup",
            amount=1500.0,
            payment_method="netbanking",
            failure_type="bank_timeout",
        )
        j2 = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_dup",
            amount=1500.0,
            payment_method="netbanking",
            failure_type="bank_timeout",
        )

        assert j1.journey_id == j2.journey_id
        count = db_session.query(RecoveryJourneyModel).filter(RecoveryJourneyModel.transaction_id == "tx_test_dup").count()
        assert count == 1


class TestRoundProgression:
    def test_round_progression_1_to_2_to_3(self, db_session: Session, journey_service: JourneyService):
        """Journey advances from Round 1 to 2, and Round 2 to 3."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_rounds",
            amount=500.0,
            payment_method="upi",
            failure_type="soft_decline",
        )
        assert journey.current_round == 1

        # Advance to Round 2
        j_r2 = journey_service.transition_round(db_session, journey.journey_id)
        assert j_r2.current_round == 2
        assert j_r2.status == "IN_PROGRESS"

        # Advance to Round 3
        j_r3 = journey_service.transition_round(db_session, journey.journey_id)
        assert j_r3.current_round == 3
        assert j_r3.status == "IN_PROGRESS"

    def test_round_3_transitions_to_exhausted_never_round_4(self, db_session: Session, journey_service: JourneyService):
        """Advancing past Round 3 marks the journey EXHAUSTED; round 4 is never permitted."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_exhaust",
            amount=500.0,
            payment_method="upi",
            failure_type="soft_decline",
        )

        journey_service.transition_round(db_session, journey.journey_id)  # -> Round 2
        journey_service.transition_round(db_session, journey.journey_id)  # -> Round 3
        assert journey.current_round == 3

        # Advancing from Round 3 -> EXHAUSTED
        j_ex = journey_service.transition_round(db_session, journey.journey_id)
        assert j_ex.status == "EXHAUSTED"
        assert j_ex.termination_reason == "MAX_ROUNDS_REACHED"
        assert j_ex.current_round == 3  # Never 4
        assert j_ex.is_terminal is True

        # Further transition attempts must fail
        with pytest.raises(ValueError, match="terminal state"):
            journey_service.transition_round(db_session, journey.journey_id)


class TestActionRecordingAndCosts:
    def test_record_action_cost_and_contact_count(self, db_session: Session, journey_service: JourneyService):
        """Action recording updates cost, payment links, and contact count according to action semantics."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_action",
            amount=2000.0,
            payment_method="card",
            failure_type="expired_card",
        )

        # Silent action: retry_later (cost: 1.0, contact: 0)
        j1 = journey_service.record_action(db_session, journey.journey_id, "retry_later", cost=1.0)
        assert j1.active_action == "retry_later"
        assert j1.cumulative_cost == 1.0
        assert j1.contact_count == 0
        assert j1.net_value == -1.0

        # Customer-facing action: recovery_link (cost: 1.5, contact: +1)
        j2 = journey_service.record_action(
            db_session,
            journey.journey_id,
            "recovery_link",
            cost=1.5,
            payment_link_id="plink_123",
            payment_link_url="https://rzp.io/i/123",
        )
        assert j2.active_action == "recovery_link"
        assert j2.active_payment_link_id == "plink_123"
        assert j2.active_payment_link_url == "https://rzp.io/i/123"
        assert j2.cumulative_cost == 2.5
        assert j2.contact_count == 1
        assert j2.net_value == -2.5

    def test_negative_cost_rejected(self, db_session: Session, journey_service: JourneyService):
        """Negative action cost raises ValueError."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_neg_cost",
            amount=1000.0,
            payment_method="card",
            failure_type="soft_decline",
        )

        with pytest.raises(ValueError, match="Execution cost cannot be negative"):
            journey_service.record_action(db_session, journey.journey_id, "retry_now", cost=-5.0)


class TestTerminalTransitionsAndFinancialInvariants:
    def test_mark_recovered_and_net_value(self, db_session: Session, journey_service: JourneyService):
        """Successful recovery updates recovered_amount and establishes net_value = recovered - cost."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_rec",
            amount=2499.0,
            payment_method="card",
            failure_type="soft_decline",
        )
        journey_service.record_action(db_session, journey.journey_id, "recovery_link", cost=1.5)

        j_rec = journey_service.mark_recovered(db_session, journey.journey_id, recovered_amount=2499.0)
        assert j_rec.status == "RECOVERED"
        assert j_rec.termination_reason == "RECOVERED"
        assert j_rec.recovered_amount == 2499.0
        assert j_rec.cumulative_cost == 1.5
        assert j_rec.net_value == 2499.0 - 1.5
        assert j_rec.is_terminal is True

    def test_mark_recovered_idempotency_no_double_counting(self, db_session: Session, journey_service: JourneyService):
        """Repeated mark_recovered calls return the same state and never double-count revenue."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_idem",
            amount=1000.0,
            payment_method="card",
            failure_type="soft_decline",
        )
        journey_service.record_action(db_session, journey.journey_id, "retry_later", cost=1.0)

        j1 = journey_service.mark_recovered(db_session, journey.journey_id, recovered_amount=1000.0)
        initial_net = j1.net_value
        assert initial_net == 999.0

        # Repeated call with same or different amount parameter
        j2 = journey_service.mark_recovered(db_session, journey.journey_id, recovered_amount=1000.0)
        assert j2.recovered_amount == 1000.0
        assert j2.net_value == initial_net

    def test_mark_stopped(self, db_session: Session, journey_service: JourneyService):
        """mark_stopped transitions journey to STOPPED with STOP_ACTION reason."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_stop",
            amount=200.0,
            payment_method="upi",
            failure_type="repeated_failure",
        )
        journey_service.record_action(db_session, journey.journey_id, "retry_now", cost=1.0)

        j_stop = journey_service.mark_stopped(db_session, journey.journey_id)
        assert j_stop.status == "STOPPED"
        assert j_stop.termination_reason == "STOP_ACTION"
        assert j_stop.recovered_amount == 0.0
        assert j_stop.net_value == -1.0

    def test_mark_escalated(self, db_session: Session, journey_service: JourneyService):
        """mark_escalated transitions journey to ESCALATED with ESCALATE_ACTION reason."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_esc",
            amount=5000.0,
            payment_method="card",
            failure_type="repeated_failure",
        )
        journey_service.record_action(db_session, journey.journey_id, "escalate_human", cost=30.0)

        j_esc = journey_service.mark_escalated(db_session, journey.journey_id)
        assert j_esc.status == "ESCALATED"
        assert j_esc.termination_reason == "ESCALATE_ACTION"
        assert j_esc.cumulative_cost == 30.0
        assert j_esc.net_value == -30.0

    def test_mark_exhausted_direct(self, db_session: Session, journey_service: JourneyService):
        """mark_exhausted transitions journey to EXHAUSTED with MAX_ROUNDS_REACHED reason."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_exh_direct",
            amount=300.0,
            payment_method="upi",
            failure_type="soft_decline",
        )

        j_ex = journey_service.mark_exhausted(db_session, journey.journey_id)
        assert j_ex.status == "EXHAUSTED"
        assert j_ex.termination_reason == "MAX_ROUNDS_REACHED"

    def test_terminal_state_immutability(self, db_session: Session, journey_service: JourneyService):
        """A journey in any terminal state cannot transition to any other state."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_immutable",
            amount=1000.0,
            payment_method="card",
            failure_type="soft_decline",
        )
        journey_service.mark_recovered(db_session, journey.journey_id)

        # RECOVERED -> STOPPED must fail
        with pytest.raises(ValueError, match="terminal state"):
            journey_service.mark_stopped(db_session, journey.journey_id)

        # RECOVERED -> ESCALATED must fail
        with pytest.raises(ValueError, match="terminal state"):
            journey_service.mark_escalated(db_session, journey.journey_id)

        # RECOVERED -> EXHAUSTED must fail
        with pytest.raises(ValueError, match="terminal state"):
            journey_service.mark_exhausted(db_session, journey.journey_id)

        # RECOVERED -> record_action must fail
        with pytest.raises(ValueError, match="terminal state"):
            journey_service.record_action(db_session, journey.journey_id, "retry_later")

    def test_negative_recovery_amount_rejected(self, db_session: Session, journey_service: JourneyService):
        """Negative recovery amount raises ValueError."""
        journey = journey_service.get_or_create_journey(
            db=db_session,
            transaction_id="tx_test_neg_rec",
            amount=1000.0,
            payment_method="card",
            failure_type="soft_decline",
        )

        with pytest.raises(ValueError, match="Recovered amount cannot be negative"):
            journey_service.mark_recovered(db_session, journey.journey_id, recovered_amount=-100.0)
