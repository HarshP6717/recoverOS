# RecoverOS — Razorpay Action Capability Matrix

**Document Version:** 1.0.0  
**Target:** Razorpay AI Buildathon — Track 03: AI Revenue Recovery  
**Purpose:** Honest classification of recovery actions between genuine Razorpay Test APIs, event simulations, and internal state engines.

---

## 1. Official Classification Taxonomy

Every RecoverOS recovery action is categorized into exactly one of four formal tiers:

- **Tier A — Real Razorpay Test-Mode API Execution:** Genuinely invokes Razorpay's live REST API endpoints (`https://api.razorpay.com/v1/...`) using valid merchant credentials (`rzp_test_...`), producing verifiable Razorpay entity IDs and hosted URLs.
- **Tier B — Razorpay Test-Mode Event Simulation:** Genuinely interacts with Razorpay webhook simulation infrastructure or test card/UPI payment state transitions.
- **Tier C — Internal RecoverOS Simulation / Queue Worker:** Handled within RecoverOS's state engine, background dunning scheduler, or simulated customer communication gateway.
- **Tier D — Not Currently Possible in Public Sandbox:** Operations restricted to production banking rails or private gateway partner APIs.

---

## 2. Comprehensive Action Capability Matrix

| Action Name | Classification Tier | Razorpay API / Webhook Mapping | Action Execution Cost (₹) | RecoverOS Role in Recovery Journey | Live Demo Feasibility |
|---|---|---|---|---|---|
| **`recovery_link`** | **Tier A: Real API Execution** | `POST /v1/payment_links` / `payment_link.paid` | ₹1.50 | Creates a personalized Razorpay hosted payment link (`https://rzp.io/i/...`) with auto-reconciliation notes. When paid, auto-resolves the recovery journey. | **100% Live Interactive Demo** (Can be opened & paid in test browser). |
| **`payment_method_update`** | **Tier A/B: Real API & Session** | `POST /v1/subscriptions/{id}/update` or secure update token | ₹2.00 | Generates a secure hosted portal session for the customer to replace expired/stolen card details or update UPI VPAs without canceling subscription. | **100% Demonstrable** (Generates secure update session URL). |
| **`retry_now`** | **Tier B: Test Event Simulation** | Re-attempts authorization / simulated gateway response | ₹1.00 | Immediate payment re-attempt for transient bank gateway timeouts (`bank_timeout`). Used only when error code indicates temporary infrastructure downtime. | **100% Demonstrable** (Simulated immediate gateway re-auth). |
| **`retry_later`** | **Tier C: Internal Queue Scheduler** | Background dunning queue / scheduled retry job | ₹1.00 | Schedules smart off-peak retry (e.g. 48–72 hours later around salary credit dates for `insufficient_funds`). Prevents immediate re-declines. | **100% Demonstrable** (Schedules delayed queue job). |
| **`send_reminder`** | **Tier A/C: Link Notification & Comms** | `notify` parameter on Payment Link / WhatsApp mock | ₹0.50 | Non-intrusive notification reminding customer of pending subscription renewal via SMS/WhatsApp with one-click payment. | **100% Demonstrable** (Notification payload generated). |
| **`escalate_human`** | **Tier C: CRM Ticket Dispatcher** | Webhook out to CRM / Zendesk / CS desk | ₹30.00 | Reserved for high-CLV accounts or complex recurring billing failures. Creates priority support ticket with complete failure diagnostic summary. | **100% Demonstrable** (Audit ledger records escalation). |
| **`stop`** | **Tier A/C: Link Cancel & State Halt** | `POST /v1/payment_links/{id}/cancel` + Internal Halt | ₹0.00 | Formally terminates dunning when recovery probability is below cost-effective threshold or fatigue limit is reached. Cancels open payment links to prevent double-charging. | **100% Demonstrable** (Cancels link, marks STOPPED). |

---

## 3. Action Portfolio Rationale & Naming Audit

### Why the 7 Actions are Structurally Sound for Razorpay
1. **Separation of Silent Retries vs. Interactive Actions:**
   - Automated backend retries (`retry_now`, `retry_later`) cost only ₹1.00 and generate zero customer communication fatigue.
   - Interactive recovery (`recovery_link`, `payment_method_update`, `send_reminder`) actively engage the customer when card details or mandate authorization must be replaced.
2. **Economic Guardrail Against Over-Escalation:**
   - `escalate_human` costs ₹30.00. RecoverOS guardrails enforce that micro-invoices (< ₹200) are **never escalated to human agents**, preventing negative expected value operations.
3. **Hard Failure Disablement:**
   - For `expired_card`, `hard_decline`, and `invalid_payment_method`, `retry_now` and `retry_later` are mathematically and logically prohibited by Guardrail 2.

---

## 4. Key Takeaways for Buildathon Evaluation

- **Honest Demarcation:** `recovery_link` and `payment_link.cancel` make genuine HTTP calls to Razorpay's live Test Mode API servers.
- **Closed-Loop Reconciliation:** When a test payment link is paid in the Razorpay sandbox, Razorpay triggers the `payment_link.paid` webhook, which RecoverOS ingests to confirm recovery in real time.
