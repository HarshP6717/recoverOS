import pytest
from backend.app.models.database import SessionLocal
from backend.scripts.resilience_lab import (
    sc_01_creation_timeout,
    sc_02_creation_5xx,
    sc_03_cancellation_timeout,
    sc_04_duplicate_webhook,
    sc_05_concurrent_duplicate_webhook,
    sc_06_invalid_hmac,
    sc_07_missing_hmac,
    sc_08_replay_webhook,
    sc_09_llm_unavailable,
    sc_10_malformed_llm_response,
    sc_11_unknown_settlement,
    sc_12_settlement_before_action,
    sc_13_stopped_journey_settlement,
    sc_14_escalated_journey_settlement,
    sc_15_exhausted_journey_settlement,
    sc_16_payment_link_cancelled,
    sc_17_invalid_amount,
    sc_18_database_failure,
)

@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()


def test_sc_01_creation_timeout(db_session):
    res = sc_01_creation_timeout(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_02_creation_5xx(db_session):
    res = sc_02_creation_5xx(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_03_cancellation_timeout(db_session):
    res = sc_03_cancellation_timeout(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_04_duplicate_webhook(db_session):
    res = sc_04_duplicate_webhook(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_05_concurrent_duplicate_webhook(db_session):
    res = sc_05_concurrent_duplicate_webhook(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_06_invalid_hmac(db_session):
    res = sc_06_invalid_hmac(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_07_missing_hmac(db_session):
    res = sc_07_missing_hmac(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_08_replay_webhook(db_session):
    res = sc_08_replay_webhook(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_09_llm_unavailable(db_session):
    res = sc_09_llm_unavailable(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_10_malformed_llm_response(db_session):
    res = sc_10_malformed_llm_response(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_11_unknown_settlement(db_session):
    res = sc_11_unknown_settlement(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_12_settlement_before_action(db_session):
    res = sc_12_settlement_before_action(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_13_stopped_journey_settlement(db_session):
    res = sc_13_stopped_journey_settlement(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_14_escalated_journey_settlement(db_session):
    res = sc_14_escalated_journey_settlement(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_15_exhausted_journey_settlement(db_session):
    res = sc_15_exhausted_journey_settlement(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_16_payment_link_cancelled(db_session):
    res = sc_16_payment_link_cancelled(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_17_invalid_amount(db_session):
    res = sc_17_invalid_amount(db_session)
    assert res["pass"] is True, res["actual_result"]


def test_sc_18_database_failure(db_session):
    res = sc_18_database_failure(db_session)
    assert res["pass"] is True, res["actual_result"]
