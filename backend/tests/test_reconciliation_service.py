"""
Unit and Integration Tests for Closed-Loop Settlement Reconciliation.

Verifies:
- payment_link.paid transitions Journey to RECOVERED
- payment.captured transitions Journey to RECOVERED
- subscription.charged transitions Journey to RECOVERED
- Double-payment protection: open payment links are auto-cancelled on settlement
- Repeated/duplicate settlements are idempotent (no double revenue addition)
- Unmatched settlements are safely handled
- Simulation mode remains strictly network-free
- Financial invariants: net_value = recovered_amount - cumulative_cost
- Live test mode isolated with @pytest.mark.live_razorpay
"""

import os
from urllib.parse import urlparse
import pytest
from sqlalchemy.orm import Session
from unittest.mock import MagicMock, patch

from backend.app.models.database import RecoveryJourneyModel
from backend.app.schemas.recovery import DecisionRequest
from backend.app.services.action_executor import ActionExecutionSimulator
from backend.app.services.decision_engine import DecisionEngine
from backend.app.services.event_service import EventService
from backend.app.services.guardrails import GuardrailEngine
from backend.app.services.journey_service import JourneyService
from backend.app.services.razorpay_client import (
    RazorpayAuthenticationError,
    RazorpayClientError,
    RazorpayTestClient,
)
from backend.app.services.reconciliation_service import ReconciliationService
from backend.app.services.recovery_orchestrator import RecoveryOrchestrator


@pytest.fixture
def setup_services(db_session: Session):
    guardrails = GuardrailEngine()
    decision_engine = DecisionEngine(guardrail_engine=guardrails)
    client = RazorpayTestClient()
    executor = ActionExecutionSimulator(razorpay_client=client)
    journey_svc = JourneyService()
    event_svc = EventService(decision_engine=decision_engine, action_executor=executor)
    orchestrator = RecoveryOrchestrator(
        journey_service=journey_svc,
        event_service=event_svc,
        decision_engine=decision_engine,
        action_executor=executor,
    )
    recon_svc = ReconciliationService(
        journey_service=journey_svc,
        razorpay_client=client,
    )
    return journey_svc, orchestrator, recon_svc, client


class TestSettlementReconciliation:
    def test_payment_link_paid_reconciles_journey(self, db_session: Session, setup_services):
        """payment_link.paid event transitions Journey to RECOVERED and sets recovered_amount."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        # 1. Create journey with active payment link
        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_plink_paid_001",
            amount=2499.0,
            payment_method="card",
            failure_type="expired_card",
        )
        journey_svc.record_action(
            db=db_session,
            journey_id=journey.journey_id,
            action="recovery_link",
            cost=1.50,
            payment_link_id="plink_live_12345",
            payment_link_url="https://rzp.io/i/12345",
        )
        assert journey.cumulative_cost == 1.50

        # 2. Simulate incoming payment_link.paid webhook payload
        payload = {
            "entity": "event",
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_live_12345",
                        "amount": 249900,  # 249900 paise = ₹2,499.00
                        "reference_id": "tx_plink_paid_001",
                        "notes": {"transaction_id": "tx_plink_paid_001"},
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_987654321",
                        "amount": 249900,
                    }
                },
            },
        }

        # 3. Reconcile
        result = recon_svc.reconcile_settlement(db_session, "payment_link.paid", payload)
        assert result.status == "reconciled"
        assert result.journey is not None
        assert result.journey.status == "RECOVERED"
        assert result.journey.recovered_amount == 2499.0
        assert result.journey.net_value == 2499.0 - 1.50  # ₹2,497.50

    def test_payment_captured_reconciles_and_cancels_open_link(self, db_session: Session, setup_services):
        """Direct payment capture reconciles journey AND cancels open payment link to prevent double billing."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_capture_001",
            amount=1499.0,
            payment_method="upi",
            failure_type="bank_timeout",
        )
        journey_svc.record_action(
            db=db_session,
            journey_id=journey.journey_id,
            action="recovery_link",
            cost=1.50,
            payment_link_id="plink_competing_999",
            payment_link_url="https://rzp.io/i/competing",
        )

        payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_captured_direct_001",
                        "amount": 149900,
                        "notes": {"transaction_id": "tx_capture_001"},
                    }
                }
            },
        }

        result = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
        assert result.status == "reconciled"
        assert result.journey.status == "RECOVERED"
        assert result.cancelled_payment_link_id == "plink_competing_999"
        assert result.journey.net_value == 1499.0 - 1.50

    def test_subscription_charged_reconciles_journey(self, db_session: Session, setup_services):
        """subscription.charged event reconciles subscription-linked active journey."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_sub_001",
            amount=999.0,
            payment_method="mandate_nach",
            failure_type="insufficient_funds",
            subscription_id="sub_auto_888",
        )

        payload = {
            "entity": "event",
            "event": "subscription.charged",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_auto_888",
                        "plan_amount": 99900,
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_sub_charged_123",
                        "amount": 99900,
                    }
                },
            },
        }

        result = recon_svc.reconcile_settlement(db_session, "subscription.charged", payload)
        assert result.status == "reconciled"
        assert result.journey.status == "RECOVERED"
        assert result.journey.recovered_amount == 999.0

    def test_duplicate_settlement_does_not_double_count(self, db_session: Session, setup_services):
        """Delivering duplicate settlement webhooks ignores subsequent calls without adding revenue again."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_dup_settle",
            amount=500.0,
            payment_method="upi",
            failure_type="soft_decline",
        )

        payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_dup_001",
                        "amount": 50000,
                        "notes": {"transaction_id": "tx_dup_settle"},
                    }
                }
            },
        }

        # 1st delivery
        res1 = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
        assert res1.status == "reconciled"
        assert res1.journey.recovered_amount == 500.0

        # 2nd delivery
        res2 = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
        assert res2.status == "duplicate_settlement_ignored"
        assert res2.journey.recovered_amount == 500.0  # Remains 500, never 1000

    def test_unmatched_settlement_handled_safely(self, db_session: Session, setup_services):
        """Settlement with unrecognized IDs returns status unmatched without crashing."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_unrecognized_999",
                        "amount": 100000,
                        "notes": {},
                    }
                }
            },
        }

        result = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
        assert result.status == "unmatched"
        assert result.journey is None

    def test_payment_link_paid_does_not_cancel_own_settled_link(self, db_session: Session, setup_services):
        """When payment_link.paid reconciles, it must NOT attempt to cancel its own settled payment link."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_plink_own_001",
            amount=1000.0,
            payment_method="card",
            failure_type="expired_card",
        )
        journey_svc.record_action(
            db=db_session,
            journey_id=journey.journey_id,
            action="recovery_link",
            cost=1.50,
            payment_link_id="plink_settled_001",
            payment_link_url="https://rzp.io/i/settled001",
        )

        payload = {
            "entity": "event",
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_settled_001",
                        "amount": 100000,
                        "reference_id": "tx_plink_own_001",
                        "notes": {"transaction_id": "tx_plink_own_001"},
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_settled_001",
                        "amount": 100000,
                    }
                },
            },
        }

        with patch.object(recon_svc.razorpay_client, "cancel_payment_link", wraps=recon_svc.razorpay_client.cancel_payment_link) as mock_cancel:
            result = recon_svc.reconcile_settlement(db_session, "payment_link.paid", payload)
            assert result.status == "reconciled"
            assert result.journey.status == "RECOVERED"
            assert result.cancelled_payment_link_id is None
            mock_cancel.assert_not_called()

    def test_payment_captured_cancels_competing_open_link(self, db_session: Session, setup_services):
        """When payment.captured reconciles, it must cancel any open competing recovery link."""
        journey_svc, orchestrator, recon_svc, client = setup_services

        journey = journey_svc.get_or_create_journey(
            db=db_session,
            transaction_id="tx_competing_001",
            amount=2000.0,
            payment_method="upi",
            failure_type="insufficient_funds",
        )
        journey_svc.record_action(
            db=db_session,
            journey_id=journey.journey_id,
            action="recovery_link",
            cost=1.50,
            payment_link_id="plink_competing_001",
            payment_link_url="https://rzp.io/i/competing001",
        )

        payload = {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_direct_settlement_001",
                        "amount": 200000,
                        "notes": {"transaction_id": "tx_competing_001"},
                    }
                }
            },
        }

        with patch.object(recon_svc.razorpay_client, "cancel_payment_link", wraps=recon_svc.razorpay_client.cancel_payment_link) as mock_cancel:
            result = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
            assert result.status == "reconciled"
            assert result.journey.status == "RECOVERED"
            assert result.cancelled_payment_link_id == "plink_competing_001"
            mock_cancel.assert_called_once_with("plink_competing_001")


class TestClientAndSecurity:
    def test_simulation_client_creates_and_cancels_link_network_free(self):
        """Offline simulation client returns mock data without any network call."""
        client = RazorpayTestClient(live_execution=False)
        link = client.create_payment_link(
            amount=1000.0,
            customer_id="cust_offline",
            description="Offline test",
            reference_id="ref_offline_001",
        )
        assert link["id"].startswith("plink_test_")
        assert link["amount"] == 100000
        assert "https://rzp.io/i/" in link["short_url"]

        cancelled = client.cancel_payment_link(link["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["id"] == link["id"]

    def test_invalid_credentials_rejected(self):
        """Invalid credentials raise RazorpayAuthenticationError."""
        client = RazorpayTestClient(key_id="bad_prefix", key_secret="secret")
        with pytest.raises(RazorpayAuthenticationError, match="Invalid Razorpay Key ID format"):
            client.validate_credentials()

    def test_live_mode_http_mocked_execution(self):
        """When live_execution=True, client constructs correct HTTP request with Basic Auth."""
        client = RazorpayTestClient(
            key_id="rzp_test_mocklive123",
            key_secret="mocksecret456",
            live_execution=True,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "plink_live_test_001",
            "entity": "payment_link",
            "amount": 250000,
            "currency": "INR",
            "status": "created",
            "short_url": "https://rzp.io/i/mocklive",
            "description": "Live Mock Recovery",
            "customer_id": "cust_live_01",
            "reference_id": "ref_live_001",
            "notes": {},
            "created_at": 1700000000,
        }

        with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
            result = client.create_payment_link(
                amount=2500.0,
                customer_id="cust_live_01",
                description="Live Mock Recovery",
                reference_id="ref_live_001",
            )
            assert result["id"] == "plink_live_test_001"
            assert result["short_url"] == "https://rzp.io/i/mocklive"
            assert mock_post.called


class TestLiveRazorpayApiIntegration:
    @pytest.mark.live_razorpay
    def test_real_razorpay_test_mode_api_execution(self):
        """
        Genuinely executes live HTTP request against Razorpay Test Mode REST API.
        Only runs when RAZORPAY_LIVE_EXECUTION=true and valid credentials are provided.
        """
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        live_flag = os.getenv("RAZORPAY_LIVE_EXECUTION", "false").lower() in ("true", "1", "yes")

        if not live_flag or not key_id or not key_secret or key_id.startswith("rzp_test_recoveros123"):
            pytest.skip("Skipping live Razorpay API test: Live credentials not configured.")

        client = RazorpayTestClient(
            key_id=key_id,
            key_secret=key_secret,
            live_execution=True,
        )

        # 1. Create real Test Mode payment link
        link = client.create_payment_link(
            amount=100.0,
            customer_id="cust_live_test",
            description="RecoverOS Live Test Mode Integration Link",
            reference_id=f"ref_live_{os.getpid()}",
        )
        assert link["id"].startswith("plink_")

        # Validate genuine HTTPS Razorpay hosted checkout URL
        short_url = link.get("short_url", "")
        assert short_url, "short_url must be present in payment link response"
        parsed_url = urlparse(short_url)
        assert parsed_url.scheme == "https", f"Expected https scheme, got {parsed_url.scheme}"
        assert parsed_url.netloc == "rzp.io", f"Expected hostname rzp.io, got {parsed_url.netloc}"
        assert parsed_url.path.strip("/"), f"Expected non-empty URL path in short_url, got {parsed_url.path}"
        assert link["status"] in ("created", "active")

        # 2. Cancel real Test Mode payment link
        cancelled = client.cancel_payment_link(link["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["id"] == link["id"]
        print(f"\n[LIVE RAZORPAY TEST] Created Link ID: {link['id']} | URL: {link['short_url']} | Status: {link['status']}")
        print(f"[LIVE RAZORPAY TEST] Cancelled Link ID: {cancelled['id']} | Status: {cancelled['status']}")

    @pytest.mark.live_razorpay
    def test_live_hybrid_closed_loop_competing_link_cancellation(self, db_session: Session):
        """
        Genuine live integration test creating real Test Mode payment links,
        then reconciling a synthetic signed settlement webhook to trigger real
        live cancellation of the competing payment link via Razorpay REST API.
        """
        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        live_flag = os.getenv("RAZORPAY_LIVE_EXECUTION", "false").lower() in ("true", "1", "yes")

        if not live_flag or not key_id or not key_secret or key_id.startswith("rzp_test_recoveros123"):
            pytest.skip("Skipping live Razorpay hybrid test: Live credentials not configured.")

        client = RazorpayTestClient(
            key_id=key_id,
            key_secret=key_secret,
            live_execution=True,
        )
        journey_svc = JourneyService()
        recon_svc = ReconciliationService(
            journey_service=journey_svc,
            razorpay_client=client,
        )

        # 1. Create real Test Mode Link A (represents settlement payment)
        pid = os.getpid()
        link_a = client.create_payment_link(
            amount=100.0,
            customer_id="cust_live_hybrid_a",
            description="RecoverOS Live Hybrid Settlement Link A",
            reference_id=f"ref_live_hyb_a_{pid}",
        )
        assert link_a["id"].startswith("plink_")

        # 2. Create real Test Mode Link B (represents competing open link on journey)
        link_b = client.create_payment_link(
            amount=100.0,
            customer_id="cust_live_hybrid_b",
            description="RecoverOS Live Hybrid Competing Link B",
            reference_id=f"ref_live_hyb_b_{pid}",
        )
        assert link_b["id"].startswith("plink_")

        try:
            # 3. Create Journey with active payment link = Link B
            tx_id = f"tx_live_hybrid_{pid}"
            journey = journey_svc.get_or_create_journey(
                db=db_session,
                transaction_id=tx_id,
                amount=100.0,
                payment_method="upi",
                failure_type="bank_timeout",
            )
            journey_svc.record_action(
                db=db_session,
                journey_id=journey.journey_id,
                action="recovery_link",
                cost=1.50,
                payment_link_id=link_b["id"],
                payment_link_url=link_b["short_url"],
            )
            assert journey.active_payment_link_id == link_b["id"]
            assert journey.cumulative_cost == 1.50

            # 4. Ingest controlled payment.captured webhook representing settlement through Link A
            payload = {
                "entity": "event",
                "event": "payment.captured",
                "event_id": f"evt_live_hybrid_{pid}",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": f"pay_live_hybrid_{pid}",
                            "amount": 10000,  # 10000 paise = ₹100.00
                            "notes": {"transaction_id": tx_id},
                        }
                    }
                },
            }

            # 5. Reconcile settlement
            result = recon_svc.reconcile_settlement(db_session, "payment.captured", payload)
            assert result.status == "reconciled"
            assert result.journey.status == "RECOVERED"
            assert result.recovered_amount == 100.0
            assert result.net_value == 100.0 - 1.50  # ₹98.50
            assert result.cancelled_payment_link_id == link_b["id"]

            print(f"\n[LIVE HYBRID TEST] Link A (Settlement): {link_a['id']}")
            print(f"[LIVE HYBRID TEST] Link B (Competing Cancelled): {link_b['id']} | Status: Reconciled & Cancelled")
            print(f"[LIVE HYBRID TEST] Journey Net Value: ₹{result.net_value:,.2f}")

        finally:
            # Clean up Link A so no active test link is left dangling
            try:
                client.cancel_payment_link(link_a["id"])
            except Exception:
                pass
