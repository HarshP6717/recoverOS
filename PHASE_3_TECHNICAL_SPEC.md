# RecoverOS Phase 3 — Technical Specification

**Document Version:** 1.0.0  
**Target:** Razorpay AI Buildathon — Track 03: AI Revenue Recovery  
**Scope:** Complete technical specification for the closed-loop Razorpay native recovery engine.

---

## 1. System Objectives

RecoverOS Phase 3 integrates the calibrated Expected Recovery Value (ERV) policy and deterministic guardrails directly with **Razorpay's native Webhook and Payment Links infrastructure** in Test Mode to create a fully autonomous, closed-loop revenue recovery system.

Key functional capabilities:
1. **Real-time Ingestion:** Ingest and verify `payment.failed`, `subscription.pending`, `subscription.halted`, `payment_link.paid`, and `payment.captured` events via HMAC-SHA256 signatures.
2. **Stateful Recovery Journeys:** Maintain multi-round recovery state (`RecoveryJourney`) with maximum 3-round bounded horizon, tracking contact fatigue, accumulated overdue days, and action history.
3. **Continuous ERV Optimization:** Evaluate candidate actions using calibrated recovery probabilities and net financial value (`ERV = Amount × P(recovery) − Cost`).
4. **Enforced Safety Guardrails:** Deterministically prevent hard-failure retries, micro-invoice human escalations, duplicate recoveries, and runaway dunning.
5. **Real Test-Mode Execution:** Issue genuine Razorpay Payment Links (`https://rzp.io/i/...`) for interactive checkout resolution.
6. **Closed-Loop Reconciliation:** Automatically capture recovery events, cancel outstanding payment links, and record precise net revenue to an immutable audit ledger.

---

## 2. Component Specifications

```
                          ┌────────────────────────┐
                          │ Razorpay Test Gateway  │
                          └───────────┬────────────┘
                                      │ Webhook (HMAC-SHA256)
                                      ▼
                      ┌─────────────────────────────────┐
                      │    Inbound Webhook Gateway      │
                      │  • Signature Verification       │
                      │  • Atomic Idempotency Filter    │
                      │  • Event Type Classifier        │
                      └───────────────┬─────────────────┘
                                      │
               ┌──────────────────────┴──────────────────────┐
               │ Failure Events                              │ Settlement Events
               ▼                                             ▼
┌─────────────────────────────┐               ┌─────────────────────────────┐
│    Event Normalization      │               │   Closed-Loop Reconciler    │
│  • Error Code Mapping       │               │  • Correlate Payment/Link   │
│  • Account Context Extract  │               │  • Mark Journey RECOVERED   │
└──────────────┬──────────────┘               │  • Cancel Pending Links     │
               │                              │  • Record Net Financial Gain│
               ▼                              └─────────────────────────────┘
┌─────────────────────────────┐
│  Stateful Journey Engine    │
│  • Check Current Round (1-3)│
│  • Update Overdue & Fatigue │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│     AI Decision Engine      │
│  • Calibrated ML Model      │
│  • 7 Candidate ERV Ranking  │
│  • Deterministic Guardrails │
└──────────────┬──────────────┘
               │ Selected Action
               ▼
┌─────────────────────────────┐
│    Hybrid Action Executor   │
│  • Tier A: Razorpay API     │ (Payment Link Creation / Cancellation)
│  • Tier B/C: Queue Worker   │ (Delayed Retries, Reminders, Escalations)
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│    Immutable Audit Ledger   │
│  • Decision Event Logs      │
│  • Execution Records        │
│  • Financial Uplift Stats   │
└─────────────────────────────┘
```

---

## 3. Database Schema & Data Models

### A. `recovery_journeys` Table
Tracks active and historical sequential recovery journeys across multiple rounds.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `journey_id` | `VARCHAR(64)` | `PRIMARY KEY` | Unique journey identifier (e.g. `jrn_9a8b7c6d5e4f`). |
| `transaction_id` | `VARCHAR(128)` | `INDEX, NOT NULL` | Razorpay payment ID or internal transaction reference. |
| `customer_id` | `VARCHAR(128)` | `INDEX, NOT NULL` | Razorpay customer ID (`cust_xxx`). |
| `subscription_id` | `VARCHAR(128)` | `INDEX, NOT NULL` | Razorpay subscription ID (`sub_xxx`). |
| `amount` | `FLOAT` | `NOT NULL` | Invoice amount in INR (₹). |
| `payment_method` | `VARCHAR(32)` | `NOT NULL` | Normalized payment method (`upi`, `card`, `netbanking`, etc.). |
| `failure_type` | `VARCHAR(64)` | `NOT NULL` | Normalized failure diagnosis (`insufficient_funds`, `expired_card`, etc.). |
| `current_round` | `INTEGER` | `DEFAULT 1` | Current active round (1, 2, or 3). |
| `status` | `VARCHAR(32)` | `NOT NULL` | `IN_PROGRESS`, `RECOVERED`, `STOPPED`, `ESCALATED`, `EXHAUSTED`. |
| `termination_reason` | `VARCHAR(64)` | `NULLABLE` | `RECOVERED`, `STOP_ACTION`, `ESCALATE_ACTION`, `MAX_ROUNDS_REACHED`. |
| `active_action` | `VARCHAR(32)` | `NULLABLE` | Latest action executed in current round. |
| `active_payment_link_id` | `VARCHAR(64)` | `NULLABLE` | Razorpay `plink_xxx` if payment link was created. |
| `active_payment_link_url` | `TEXT` | `NULLABLE` | Hosted checkout URL `https://rzp.io/i/...`. |
| `cumulative_cost` | `FLOAT` | `DEFAULT 0.0` | Sum of all action execution costs in INR. |
| `recovered_amount` | `FLOAT` | `DEFAULT 0.0` | Amount recovered if successful, else 0.0. |
| `net_value` | `FLOAT` | `DEFAULT 0.0` | `recovered_amount - cumulative_cost`. |
| `contact_count` | `INTEGER` | `DEFAULT 0` | Accumulated customer-facing contacts. |
| `days_overdue` | `FLOAT` | `DEFAULT 0.0` | Accumulated days since initial failure. |
| `created_at` | `DATETIME` | `NOT NULL` | Timestamp of journey creation. |
| `updated_at` | `DATETIME` | `NOT NULL` | Timestamp of latest state transition. |

### B. `recovery_events` Table (Immutable Audit Ledger)
Stores full ML decision details, feature vectors, candidate ERVs, and guardrail logs.

### C. `processed_webhooks` Table (Atomic Idempotency)
Stores `webhook_event_id` with a database `UNIQUE` constraint.

---

## 4. REST API Endpoint Specifications

### 1. `POST /v1/webhooks/razorpay`
- **Purpose:** Ingestion gateway for all Razorpay webhook traffic.
- **Headers:** `X-Razorpay-Signature`, `X-Razorpay-Event-Id`.
- **Processing:**
  1. Validates HMAC signature using `RAZORPAY_WEBHOOK_SECRET`.
  2. Ensures atomic idempotency via `processed_webhooks` table.
  3. If event is `payment.failed`, `subscription.pending`, or `subscription.halted`:
     - Creates or updates `RecoveryJourney`.
     - Executes ERV policy & guardrails.
     - Triggers action execution (e.g. calls Razorpay Payment Links API).
  4. If event is `payment_link.paid`, `payment.captured`, or `subscription.charged`:
     - Correlates to active `RecoveryJourney`.
     - Transitions journey to `RECOVERED`.
     - Cancels outstanding links.
     - Records net revenue recovered.

### 2. `GET /v1/recovery/journeys`
- **Purpose:** Fetches active and resolved recovery journeys with filters (`status`, `failure_type`, `round`).

### 3. `GET /v1/recovery/journeys/{journey_id}`
- **Purpose:** Detailed diagnostic view of a specific recovery journey including multi-round action timeline, ERV breakdown, and audit records.

### 4. `POST /v1/recovery/simulate-failure`
- **Purpose:** Demo trigger endpoint to simulate an end-to-end incoming Razorpay failure and observe autonomous recovery.

### 5. `GET /v1/recovery/metrics`
- **Purpose:** Real-time financial summary: total revenue at risk, recovered revenue, action costs, net financial gain, and recovery rate.
