# RecoverOS Phase 3 — 5-Minute Live Interactive Demo Script

**Target Audience:** Razorpay AI Buildathon Judges, Engineers, and Evaluators  
**Goal:** Deliver a compelling, transparent, and verifiable demonstration of AI Revenue Recovery in action within 5 minutes.

---

## 1. Demo Overview & Narrative Arc

```
[Minute 1] Revenue at Risk Appears -> Simulated/Triggered Razorpay Subscription Failure
[Minute 2] Autonomous Ingestion -> Error Diagnosis -> 7-Action ERV Ranking & Guardrails
[Minute 3] Genuine Razorpay API Execution -> Live Hosted Checkout Link Generated
[Minute 4] Interactive Test Payment -> Closed-Loop Webhook Ingestion & Reconciliation
[Minute 5] Real-Time Net Revenue Verified -> Complete Audit Trail & Explainability
```

---

## 2. Step-by-Step Demo Execution Script

### Step 1: Revenue at Risk Generated (0:00 - 1:00)
- **Action:** An operator or script triggers a failed recurring payment webhook representing an annual SaaS subscription failure:
  - **Transaction:** `pay_rzp_live_884920`
  - **Invoice Amount:** **₹2,499.00**
  - **Customer:** Priya Patel (`cust_delhi_102`)
  - **Payment Method:** `card` (HDFC Credit Card)
  - **Failure Reason:** `CARD_EXPIRED` (Card validity expired)
  - **Customer Profile:** CLV ₹27,489.00, 11 successful past payments, 0 recent contacts.
- **Visual:** The RecoverOS console highlights an active **Revenue at Risk Alert: ₹2,499.00**.

---

### Step 2: Autonomous Ingestion & AI Diagnosis (1:00 - 2:00)
- **Action:** RecoverOS ingests the webhook at `/v1/webhooks/razorpay`:
  1. Verifies the `X-Razorpay-Signature` HMAC-SHA256 hash.
  2. Confirms atomic idempotency in `processed_webhooks`.
  3. Maps `CARD_EXPIRED` to internal category `expired_card`.
  4. Initializes `RecoveryJourney` (ID: `jrn_exp_9921`, Round: 1).
- **Visual (Decision Engine Modal):**
  - **Candidate Action Rankings:**
    - `recovery_link`: Predicted Probability = **74.0%**, ERV = **₹1,847.76** (Cost: ₹1.50) -> **SELECTED (Rank 1)**
    - `payment_method_update`: Predicted Probability = **82.0%**, ERV = **₹2,047.18** (Cost: ₹2.00) -> Candidate
    - `escalate_human`: Predicted Probability = **72.0%**, ERV = **₹1,769.28** (Cost: ₹30.00)
    - `send_reminder`: Predicted Probability = **32.0%**, ERV = **₹799.18** (Cost: ₹0.50)
    - `retry_now`: Predicted Probability = 1.0% -> **BLOCKED BY GUARDRAIL 2** (Permanent Failure Retry Suppression)
    - `retry_later`: Predicted Probability = 2.0% -> **BLOCKED BY GUARDRAIL 2** (Permanent Failure Retry Suppression)
    - `stop`: ERV = ₹0.00

---

### Step 3: Real Razorpay Test API Execution (2:00 - 3:00)
- **Action:** RecoverOS dispatches the action to Razorpay Test Mode REST API:
  - Invokes `POST https://api.razorpay.com/v1/payment_links` using credentials `rzp_test_...`.
  - Attaches reconciliation metadata in `notes` (`recoveros_journey_id: jrn_exp_9921`).
- **Result:** Razorpay returns an active hosted payment link entity:
  - **Link ID:** `plink_test_77a9b0c1`
  - **Hosted URL:** `https://rzp.io/i/77a9b0c1`
- **Visual:** The RecoverOS dashboard presents a clickable link button: **"Open Razorpay Test Checkout"**.

---

### Step 4: Interactive Test Payment & Closed-Loop Webhook (3:00 - 4:00)
- **Action:** The judge or presenter clicks the link in a browser:
  1. The official Razorpay hosted checkout page opens.
  2. The presenter selects **UPI / Card**, enters `success@razorpay`, and clicks **Pay ₹2,499.00**.
  3. Razorpay processes the test transaction and generates a real `payment_link.paid` webhook event.
- **Webhook Ingestion:**
  - RecoverOS receives `payment_link.paid` with reference `plink_test_77a9b0c1`.
  - Correlates the payment ID to active journey `jrn_exp_9921`.
  - Transitions journey status from `IN_PROGRESS` to **`RECOVERED`**.
  - Automatically cancels any secondary dunning reminders.

---

### Step 5: Net Revenue Accounting & Audit Provenance (4:00 - 5:00)
- **Action:** The RecoverOS console updates live:
  - **Gross Recovered:** **₹2,499.00**
  - **Action Execution Cost:** **₹1.50**
  - **Net Financial Value Added:** **+₹2,497.50**
  - **Journey Duration:** 42 seconds from failure to resolution.
  - **Audit Provenance:** Complete cryptographic trace from initial `payment.failed` event, ML feature vector, guardrail verification, gateway call, to final `payment.captured` event.

---

## 3. Secondary Safety Scenario: Hard Stop on Unviable Debt

- **Scenario:** Micro-invoice failure (₹199.00) after 3 failed attempts, contact count = 6, days overdue = 14.
- **Demonstration:**
  - ML model predicts recovery probability < 2.0%.
  - Expected Recovery Value for all actions is negative (`ERV < 0`).
  - Guardrail 1 blocks human escalation (`amount < ₹200`).
  - Decision engine chooses **`stop`**.
  - RecoverOS halts dunning cleanly without customer harassment, logging zero wasted cost.
