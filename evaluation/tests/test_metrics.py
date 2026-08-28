"""
Tests for evaluation/metrics.py.

Covers:
- Metric correctness (known values)
- Zero-division safety
- Correct handling of optional fields (predicted_erv, guardrails_triggered)
- Edge cases: empty input, all-stop, all-recovered
"""

from __future__ import annotations

import pytest
from evaluation.metrics import compute_policy_metrics, safe_divide


# ── safe_divide ───────────────────────────────────────────────────────────────


class TestSafeDivide:
    def test_normal_division(self):
        assert safe_divide(10.0, 4.0) == pytest.approx(2.5)

    def test_zero_denominator_returns_default(self):
        assert safe_divide(100.0, 0.0) == 0.0

    def test_zero_denominator_custom_default(self):
        assert safe_divide(5.0, 0.0, default=-1.0) == -1.0

    def test_zero_numerator(self):
        assert safe_divide(0.0, 10.0) == 0.0

    def test_negative_values(self):
        assert safe_divide(-20.0, 4.0) == pytest.approx(-5.0)


# ── compute_policy_metrics ────────────────────────────────────────────────────


def _make_record(
    index: int,
    action: str,
    recovered: bool,
    amount: float = 1000.0,
    cost: float = 1.0,
    predicted_erv=None,
    guardrails_triggered=None,
) -> dict:
    recovered_amount = amount if recovered else 0.0
    net_value = recovered_amount - cost
    return {
        "index": index,
        "action": action,
        "recovered": recovered,
        "recovered_amount": recovered_amount,
        "action_cost": cost,
        "net_value": net_value,
        "recovery_probability_gt": 0.5,
        "predicted_erv": predicted_erv,
        "guardrails_triggered": guardrails_triggered,
    }


class TestComputePolicyMetrics:
    def test_empty_records_returns_zeros(self):
        m = compute_policy_metrics([], policy_name="Empty")
        assert m["n_cases"] == 0
        assert m["recovered_count"] == 0
        assert m["recovery_rate"] == 0.0
        assert m["total_net_value"] == 0.0
        assert m["avg_net_value_per_case"] == 0.0
        assert m["stop_rate"] == 0.0

    def test_all_recovered(self):
        records = [_make_record(i, "retry_now", True) for i in range(4)]
        m = compute_policy_metrics(records, "Test")
        assert m["n_cases"] == 4
        assert m["recovered_count"] == 4
        assert m["recovery_rate"] == pytest.approx(1.0)
        # Each: net_value = 1000 - 1 = 999
        assert m["total_net_value"] == pytest.approx(4 * 999.0)
        assert m["avg_net_value_per_case"] == pytest.approx(999.0)

    def test_none_recovered(self):
        records = [_make_record(i, "stop", False, cost=0.0) for i in range(3)]
        m = compute_policy_metrics(records, "Test")
        assert m["recovered_count"] == 0
        assert m["recovery_rate"] == 0.0
        assert m["total_net_value"] == pytest.approx(0.0)
        assert m["stop_count"] == 3
        assert m["stop_rate"] == pytest.approx(1.0)

    def test_mixed_outcomes_correctness(self):
        # 2 recovered (retry_now, cost=1), 1 stop (cost=0)
        records = [
            _make_record(0, "retry_now", True, amount=500.0, cost=1.0),
            _make_record(1, "retry_now", True, amount=700.0, cost=1.0),
            _make_record(2, "stop", False, amount=200.0, cost=0.0),
        ]
        m = compute_policy_metrics(records, "Test")
        assert m["recovered_count"] == 2
        assert m["recovery_rate"] == pytest.approx(2 / 3)
        # net values: 499, 699, 0 → total = 1198
        assert m["total_net_value"] == pytest.approx(1198.0)
        assert m["avg_net_value_per_case"] == pytest.approx(1198.0 / 3)
        assert m["stop_count"] == 1
        assert m["stop_rate"] == pytest.approx(1 / 3)
        # Action distribution
        assert m["action_distribution"]["retry_now"] == 2
        assert m["action_distribution"]["stop"] == 1

    def test_total_action_cost_correct(self):
        records = [
            _make_record(0, "escalate_human", False, amount=500.0, cost=30.0),
            _make_record(1, "send_reminder", True, amount=300.0, cost=0.5),
        ]
        m = compute_policy_metrics(records, "Test")
        assert m["total_action_cost"] == pytest.approx(30.5)

    def test_total_recovered_amount_correct(self):
        records = [
            _make_record(0, "retry_now", True, amount=1000.0, cost=1.0),
            _make_record(1, "retry_now", False, amount=500.0, cost=1.0),
        ]
        m = compute_policy_metrics(records, "Test")
        assert m["total_recovered_amount"] == pytest.approx(1000.0)

    def test_avg_predicted_erv_when_present(self):
        records = [
            _make_record(0, "retry_now", True, predicted_erv=200.0),
            _make_record(1, "retry_now", False, predicted_erv=100.0),
        ]
        m = compute_policy_metrics(records, "Test")
        assert m["avg_predicted_erv"] == pytest.approx(150.0)

    def test_avg_predicted_erv_is_none_when_absent(self):
        records = [_make_record(i, "retry_now", True) for i in range(2)]
        m = compute_policy_metrics(records, "Test")
        assert m["avg_predicted_erv"] is None

    def test_guardrails_total_when_present(self):
        records = [
            _make_record(0, "retry_now", True, guardrails_triggered=1),
            _make_record(1, "send_reminder", False, guardrails_triggered=2),
        ]
        m = compute_policy_metrics(records, "Test")
        assert m["guardrails_triggered_total"] == 3

    def test_guardrails_total_none_when_absent(self):
        records = [_make_record(i, "retry_now", True) for i in range(2)]
        m = compute_policy_metrics(records, "Test")
        assert m["guardrails_triggered_total"] is None

    def test_per_action_recovery_breakdown(self):
        records = [
            _make_record(0, "retry_now", True, amount=500.0, cost=1.0),
            _make_record(1, "retry_now", False, amount=300.0, cost=1.0),
            _make_record(2, "send_reminder", True, amount=200.0, cost=0.5),
        ]
        m = compute_policy_metrics(records, "Test")
        rn = m["action_recovery_breakdown"]["retry_now"]
        assert rn["count"] == 2
        assert rn["recovered"] == 1
        assert rn["recovery_rate"] == pytest.approx(0.5)
        # net for retry_now: (500-1=499) + (0-1=-1) = 498
        assert rn["total_net_value"] == pytest.approx(498.0)

        sr = m["action_recovery_breakdown"]["send_reminder"]
        assert sr["count"] == 1
        assert sr["recovered"] == 1
        # net: 200 - 0.5 = 199.5
        assert sr["total_net_value"] == pytest.approx(199.5)

    def test_policy_name_preserved(self):
        m = compute_policy_metrics([], "My Policy")
        assert m["policy_name"] == "My Policy"
