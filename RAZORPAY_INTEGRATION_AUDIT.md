# RecoverOS — Razorpay Native Integration & Webhook Capability Audit

**Document Version:** 1.0.0  
**Target:** Razorpay AI Buildathon — Track 03: AI Revenue Recovery  
**Status:** READ-ONLY Architectural Audit & Capability Analysis  

---

## 1. Executive Summary & Mission Alignment

The official objective for Track 03 (AI Revenue Recovery) is:
> **"Detect revenue at risk → Determine the right intervention → Execute a bounded recovery workflow."**

The evaluation bar explicitly demands:
1. **Measured money recovered across a batch** (closed-loop net financial uplift).
2. **Compliant escalation** (routing high-value or stubborn failures to human teams safely).
3. **Stopping rules** (halting dunning when expected recovery value is negative or customer fatigue is reached).
4. **Audit trail** (end-to-end provenance of failure events, ML probability scores, guardrail enforcements, and gateway calls).

This audit evaluates the genuine capabilities of the **Razorpay API and Webhook infrastructure** in **Test Mode** (`rzp_test_...`), separating genuine API executions from event simulations, and establishes the architecture for Phase 3.

---

## 2. Razorpay Webhook Event Ingestion Audit

| Webhook Event Name | Payload Entity | Key Fields Extracted | RecoverOS Ingestion Role | Closed-Loop Action |
|---|---|---|---|---|
| `payment.failed` | `payload.payment.entity` | `id` (`pay_xxx`), `amount` (paise), `currency`, `method`, `customer_id`, `order_id`, `error_code`, `error_description`, `error_reason`, `notes` | **Primary Revenue-at-Risk Trigger.** Ingested, signature-verified, normalized into `DecisionRequest`. | Starts Recovery Journey (Round 1). |
| `subscription.pending` | `payload.subscription.entity` | `id` (`sub_xxx`), `plan_id`, `customer_id`, `status` ("pending"), `charge_at`, `current_start`, `current_end` | **Secondary Dunning Trigger.** Signals recurring subscription debit attempt failure. | Updates state, checks scheduled retry timing. |
| `subscription.halted` | `payload.subscription.entity` | `id` (`sub_xxx`), `status` ("halted"), `retry_count`, `ended_at` | **Terminal Dunning Alert.** Signals all gateway auto-retries failed. | Triggers high-priority escalation or clean stopping. |
| `subscription.charged` | `payload.subscription.entity` + `payload.payment.entity` | `id` (`sub_xxx`), `payment_id`, `amount`, `status` ("active") | **Closed-Loop Success Signal.** Confirms recurring subscription payment collected. | **Marks journey RECOVERED**, cancels open payment links. |
| `payment.captured` | `payload.payment.entity` | `id` (`pay_xxx`), `amount`, `order_id`, `invoice_id`, `status` ("captured") | **Closed-Loop Success Signal.** Confirms invoice/payment settled. | **Marks journey RECOVERED**, halts further dunning. |
| `payment_link.paid` | `payload.payment_link.entity` + `payload.payment.entity` | `id` (`plink_xxx`), `amount_paid`, `customer_id`, `order_id`, `status` ("paid") | **Closed-Loop Payment Link Success.** Customer paid via hosted checkout URL. | **Marks journey RECOVERED**, logs net value. |
| `payment_link.cancelled` | `payload.payment_link.entity` | `id` (`plink_xxx`), `status` ("cancelled") | **Cancellation Confirmation.** Confirms safety stop of open link. | Updates audit ledger. |
| `order.paid` | `payload.order.entity` | `id` (`order_xxx`), `amount_paid`, `status` ("paid") | **Order Settlement Confirmation.** Backup settlement signal. | Correlates payment to order. |

---

## 3. Razorpay API Capabilities in Test Mode

### A. Hosted Payment Links API (`/v1/payment_links`)
- **Integration Status:** **Implemented & Code-Complete** (Simulated locally by default; executable against live Razorpay Test Mode REST API when `RAZORPAY_LIVE_EXECUTION=true`).
- **Endpoint:** `POST https://api.razorpay.com/v1/payment_links`
- **Authentication:** Basic Auth (`key_id` : `key_secret`).
- **Request Payload:**
  ```json
  {
    "amount": 249900,
    "currency": "INR",
    "accept_partial": false,
    "description": "RecoverOS Payment Recovery - Invoice #INV-8921",
    "customer": {
      "name": "Aarav Sharma",
      "email": "aarav.sharma@example.com",
      "contact": "+919876543210"
    },
    "notify": {
      "sms": false,
      "email": false
    },
    "reminder_enable": false,
    "notes": {
      "recoveros_event_id": "evt_rzp_01",
      "recovery_action": "recovery_link"
    },
    "callback_url": "https://recoveros.app/recovery/callback",
    "callback_method": "get"
  }
  ```
- **Response Payload:** Contains genuine hosted short URL `https://rzp.io/i/{id}`.
- **Interactive Test Execution:** When live mode is enabled, a judge or developer can open the generated URL in any browser, select **Test Mode Cards / Test UPI (success@razorpay)**, submit the payment, and observe Razorpay firing real `payment_link.paid` and `payment.captured` webhooks back to RecoverOS. See [LIVE_MODE.md](LIVE_MODE.md) for live verification instructions.

### B. Payment Link Cancellation API (`/v1/payment_links/{id}/cancel`)
- **Integration Status:** **Implemented & Code-Complete** (Simulated locally by default; executable live against `api.razorpay.com` when `RAZORPAY_LIVE_EXECUTION=true`).
- **Endpoint:** `POST https://api.razorpay.com/v1/payment_links/{plink_id}/cancel`
- **Role:** When a customer pays via an alternate method, or when the policy selects `stop`, RecoverOS immediately calls this API to cancel open links, preventing double-billing.

### C. Webhook Signature Verification & Security
- **Algorithm:** `HMAC-SHA256(raw_request_body, webhook_secret) == X-Razorpay-Signature`
- **Security Requirement:** Mandatory on every inbound webhook. Reject HTTP 401 if missing or invalid.
- **Idempotency Requirement:** Webhooks may be retried by Razorpay up to 24 hours. RecoverOS enforces atomic idempotency via database `UNIQUE` constraint on `webhook_event_id`.

---

## 4. Closed-Loop Event Correlation Architecture

To prevent duplicate recoveries, out-of-order execution, and dangling payment requests, RecoverOS implements a stateful **Recovery Journey Correlation Engine**:

```
Inbound payment.failed (pay_123, sub_456)
  │
  ▼
Create RecoveryJourney (journey_id: jrn_001)
  │
  ├──► Round 1: AI selects 'recovery_link'
  │     │
  │     └──► Calls Razorpay API: POST /v1/payment_links
  │           └──► Link Created (plink_789, https://rzp.io/i/xyz)
  │
  ├──► Inbound payment_link.paid / payment.captured (plink_789 / pay_999)
  │     │
  │     ├──► Correlate: plink_789 belongs to jrn_001
  │     ├──► State Transition: jrn_001 -> RECOVERED
  │     ├──► Cancel Any Other Scheduled Retries / Dunning Queues
  │     └──► Record Net Revenue Recovered: ₹2,499.00 - ₹1.50 (cost) = ₹2,497.50
  │
  └──► If Unpaid after timeout:
        └──► Progress to Round 2 (Next Action Selection)
```

---

## 5. Summary of Integration Capabilities

| Component | Status in RecoverOS | Execution Environment |
|---|---|---|
| Inbound Webhook Gateway | **Implemented & Active** | Verifies genuine Razorpay HMAC-SHA256 signatures |
| Hosted Payment Links API | **Implemented & Code-Complete** | Deterministic simulation by default; live `rzp.io` creation via `RAZORPAY_LIVE_EXECUTION=true` |
| Link Cancellation API | **Implemented & Code-Complete** | Deterministic simulation by default; live cancellation via `RAZORPAY_LIVE_EXECUTION=true` |
| Atomic Idempotency Filter | **Implemented & Active** | Database UNIQUE constraint eliminating duplicate deliveries |
| Sequential State Engine | **Validated in Pipeline** | 3-round bounded horizon with contact fatigue tracking |
| Out-of-Order Closed Loop | **Implemented & Active** | Automatic cancellation of dunning upon payment capture |
