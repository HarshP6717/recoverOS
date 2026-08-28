# RecoverOS Phase 3 — Step-by-Step Implementation Plan

**Document Version:** 1.0.0  
**Target:** Razorpay AI Buildathon — Track 03: AI Revenue Recovery  
**Objective:** Deliver an end-to-end autonomous, closed-loop Razorpay revenue recovery system.

---

## 1. Implementation Principles

1. **Safety & Zero Disruption:** Keep all Phase 1, Phase 2A, Phase 2B, and Phase 2C code intact. Extend via modular, backward-compatible services.
2. **Honest Demarcation:** Genuinely execute real Razorpay REST API endpoints in Test Mode for `payment_links` and cancellation, while clearly isolating simulated queue delays.
3. **Closed-Loop Reconciliation:** Ensure every recovery action has an explicit settlement path that terminates open journeys and cancels pending links.
4. **Complete Auditability:** Persist every state transition, candidate ranking, guardrail trigger, and gateway response to SQLite ledger.

---

## 2. Step-by-Step Build Order

### Step 1: Stateful Recovery Journey Service & Database Migration
- **Target File:** `backend/app/models/database.py` (Add `RecoveryJourneyModel`), `backend/app/services/journey_service.py` (New).
- **Deliverables:**
  - Define `recovery_journeys` table tracking `journey_id`, `transaction_id`, `amount`, `current_round`, `status`, `active_payment_link_id`, `cumulative_cost`, and `net_value`.
  - Implement `JourneyService` with methods: `get_or_create_journey()`, `transition_round()`, `mark_recovered()`, `mark_stopped()`, `mark_escalated()`.

### Step 2: Live Razorpay REST Client & Hosted Links
- **Target File:** `backend/app/services/razorpay_client.py`.
- **Deliverables:**
  - Enhance `RazorpayTestClient` to make real HTTP calls to `https://api.razorpay.com/v1/payment_links` using `requests` / `httpx` when valid `rzp_test_...` credentials are present.
  - Implement `cancel_payment_link(payment_link_id)` using `POST /v1/payment_links/{id}/cancel`.
  - Fall back gracefully to mock entity generator if no credentials are configured.

### Step 3: Closed-Loop Reconciliation & Double-Payment Prevention
- **Target File:** `backend/app/services/reconciliation_service.py` (New).
- **Deliverables:**
  - Process settlement webhooks (`payment.captured`, `payment_link.paid`, `subscription.charged`).
  - Correlate settlement to active `RecoveryJourney` by `payment_link_id`, `order_id`, or `subscription_id`.
  - Mark journey `RECOVERED`, compute net financial gain, and cancel any open secondary links.

### Step 4: Webhook Gateway Multi-Event Ingestion
- **Target File:** `backend/app/api/routes_webhooks.py`.
- **Deliverables:**
  - Expand supported events to include `payment_link.paid`, `payment.captured`, `subscription.charged`, `subscription.halted`.
  - Route failure events to `JourneyService` -> `DecisionEngine` -> `ActionExecutor`.
  - Route settlement events to `ReconciliationService`.

### Step 5: Decision Engine Heuristic Fallback Integration
- **Target File:** `backend/app/services/decision_engine.py`.
- **Deliverables:**
  - Wrap model inference with an automated fallback to `StrongFeatureAwareHeuristic` to guarantee zero-downtime execution even under missing features or corrupted artifacts.

### Step 6: Live Control Console UI
- **Target File:** `frontend/` or standalone lightweight interactive web dashboard.
- **Deliverables:**
  - Visual interface displaying:
    1. **Live Revenue at Risk Feed** (inbound failures).
    2. **Failure Diagnosis & Candidate ERV Breakdown** (probabilities, costs, guardrail badges).
    3. **Interactive Razorpay Hosted Checkout Link Button**.
    4. **Sequential Journey Timeline** (Round 1 -> Round 2 -> Round 3 transitions).
    5. **Immutable Audit Ledger Viewer** (cryptographic event logs & financial stats).

### Step 7: Automated Integration Tests
- **Target File:** `backend/tests/test_phase3_closed_loop.py`, `backend/tests/test_reconciliation.py`.
- **Deliverables:**
  - 100% test coverage for end-to-end failure -> decision -> link creation -> test payment -> reconciliation -> ledger audit.

### Step 8: End-to-End Live Demonstration Script
- **Target File:** `backend/demonstrate_phase3_loop.py`.
- **Deliverables:**
  - Automated terminal demo demonstrating single-command 5-minute judge walkthrough with live Razorpay Test Mode checkout link.

---

## 3. Verification Plan

```bash
# 1. Run full test suite across repository (all previous 206 tests + new Phase 3 tests)
python -m pytest -v

# 2. Run Phase 3 Closed-Loop Live Demonstration
python backend/demonstrate_phase3_loop.py

# 3. Launch RecoverOS FastAPI Server & Live Console
uvicorn backend.app.main:app --reload --port 8000
```
