# RecoverOS — Technical & Architectural Frequently Asked Questions

This document addresses key technical, algorithmic, and architectural questions regarding the design, safety guarantees, and distributed systems behavior of RecoverOS.

---

### 1. Why isn't this just retry automation?
Standard retry automation treats all failures uniformly, attempting brute-force re-authorization on a static schedule. RecoverOS models recovery as an economic optimization problem. It uses semantic intelligence to estimate recovery probabilities per intervention channel and calculates an Expected Recovery Value (ERV) that factors in execution costs and customer friction. This prevents burning customer goodwill or wasting fees on unrecoverable failures (e.g., fraud or expired credentials).

### 2. Why does payment recovery need semantic AI?
Traditional rules engines struggle with the unstructured, variable error descriptions emitted across banks, card networks, and payment methods. Large Language Models excel at semantic normalization—mapping varied error strings and contextual histories into standardized failure categories and probability distributions.

### 3. What is the exact boundary of the AI?
The AI acts strictly as an unprivileged **Diagnosis Engine**. Given the failure event, customer history, and error string, it outputs a validated JSON schema containing:
1. Standardized failure category (e.g., `insufficient_funds`, `bank_timeout`).
2. Confidence score ($0.0 \dots 1.0$).
3. Probability estimations across recovery channels (`payment_link`, `retry`, `escalate`, `no_action`).
4. Concise reasoning summary.

### 4. Can the AI accidentally move money or trigger unauthorized actions?
**No.** The AI has zero execution authority. It cannot invoke gateway APIs, mutate database ledgers, or dispatch customer notifications. It emits probabilities into the deterministic `DecisionEngine`, where business guardrails and ERV arithmetic make the final decision. Only standard, auditable application code interacts with external APIs.

### 5. How does the system handle gateway timeouts or outages?
The `RazorpayTestClient` encapsulates error handling. If an API call times out (HTTP 504) or encounters gateway downtime (HTTP 502/503), the `ActionExecutor` records the state as `EXECUTION_UNKNOWN` or `GATEWAY_DOWN` and safely queues for retry without corrupting ledger state.

### 6. What happens when concurrent workers receive the same webhook?
RecoverOS enforces atomic idempotency at the database layer. The `webhook_events` table enforces a `UNIQUE` constraint on `webhook_event_id`. When a webhook arrives, the system attempts to insert a reservation row atomically. If a concurrent worker attempts the same event ID, the database raises an `IntegrityError`, and the duplicate is immediately acknowledged (HTTP 200) without double-processing.

### 7. What happens if a settlement arrives after a journey is marked STOPPED?
If a customer completes payment via an existing link after the journey was marked `STOPPED` or `EXHAUSTED`, the `ReconciliationService` still ingests the `payment_link.paid` or `payment.captured` webhook. It correlates the payment to the journey, updates the recovered amount, transitions the status to `RECOVERED`, and logs the net financial gain.

### 8. What happens during an AI provider outage?
The `DiagnosisEngine` gracefully degrades. If the LLM provider times out (5.0s) or fails, the engine falls back to a deterministic `MockDiagnosisProvider` or applies safe defaults (`confidence=0.1`, `unknown` category). Under degraded confidence, guardrails suppress automated charges and select safe non-destructive fallbacks.

### 9. What happens if the AI emits malformed JSON?
The provider layer uses Pydantic schema validation. If the LLM response violates schema or fails parsing, the system immediately catches the `ValidationError`, logs the error, and falls back to deterministic safe defaults.

### 10. Can the system double-create payment links for the same failure?
No. The `GuardrailEngine` and `JourneyService` track historical actions per journey round. If an active `recovery_link` already exists for the transaction, redundant link creations are suppressed.

### 11. How is business value quantified?
Business value is measured via **Net Recovered Value**:
$$\text{Net Value} = \sum \text{Reconciled Gross Recovered Revenue} - \sum \text{Direct API Costs} - \sum \text{Friction Penalties}$$

### 12. What are the assumptions behind the benchmark results?
The headline benchmark (`backend/scripts/evaluate_batch.py`) evaluates 1,000 synthetic payment failure scenarios against an isolated hidden ground-truth outcome model using fixed seed 42. Recovery outcomes are decoupled from predicted probabilities. It is designed for controlled algorithmic comparison against standard 3x static retry policies, not as evidence of production Razorpay conversion rates.

### 13. What is genuinely executed against live APIs vs. simulated?
When `RAZORPAY_LIVE_EXECUTION=true` with valid test credentials (`rzp_test_...`), RecoverOS makes real HTTP calls to the live Razorpay Test Mode REST API (`https://api.razorpay.com/v1/payment_links` and cancellation). In default mode (`RAZORPAY_LIVE_EXECUTION=false`), it executes deterministic local sandbox simulations for offline reproducibility.

### 14. Which actions are simulated vs. recommendation-only?
- **Live REST API:** `recovery_link` (creates hosted `rzp.io` checkout links) and `stop` (cancels active payment links via API).
- **Recommendation-Only:** `payment_method_update` generates customer update guidance and session tokens (public sandbox lacks direct mandate replacement endpoints).
- **Local Simulation:** `retry_now` (immediate re-auth), `retry_later` (scheduled dunning queue), `send_reminder` (notification dispatch), and `escalate_human` (concierge dispatch).

### 15. Why is ERV superior to static decision trees?
Static decision trees cannot dynamically weight continuous parameters like Customer Lifetime Value against varying invoice amounts and multi-channel friction costs. ERV naturally adapts: a high-friction or high-cost action may be suppressed for a ₹50 transaction but approved for a ₹50,000 transaction.

### 16. What is the Counterfactual Advantage?
The Counterfactual Advantage is the quantified marginal economic benefit of selecting the optimal ERV action over the "Next Best Action" (the runner-up permitted candidate):
$$\text{Counterfactual Advantage} = \text{ERV}(\text{Selected Action}) - \text{ERV}(\text{Next Best Action})$$

### 17. What happens when the cost of recovery exceeds the recoverable amount?
Guardrail 1 (Negative ERV Suppression) blocks the action. If recovering a ₹5 invoice incurs ₹1.50 in fees and ₹10 in customer friction, the predicted ERV is negative, and the system halts (`stop`) to prevent net financial loss.

### 18. How does the operator audit decisions in the UI?
The Merchant Command Center provides full explainability on the Journey Investigation page, presenting:
- Root cause diagnosis and AI confidence score.
- Full 7-candidate ERV ranking with direct costs and friction penalties.
- Triggered guardrail badges.
- Counterfactual advantage comparison.
- Cryptographic timeline of all ledger events.

### 19. How does this integrate with Razorpay subscriptions?
RecoverOS ingests `subscription.pending`, `subscription.halted`, and `subscription.charged` webhooks. On `subscription.halted`, Guardrail 6 suppresses generic retries and routes the issue to mandate update guidance or high-priority human escalation.

### 20. How is double-charging prevented when a customer pays?
Upon receiving any valid settlement webhook (`payment_link.paid`, `payment.captured`), the `ReconciliationService` immediately marks the journey `RECOVERED` and calls the Razorpay cancellation API for any competing active payment links.
