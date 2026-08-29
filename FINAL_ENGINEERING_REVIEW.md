# Final Adversarial Engineering Review

As requested by the master directive, this document answers 20 adversarial engineering questions that a Razorpay senior engineer would ask when evaluating RecoverOS.

## 1. Why isn't this just retry automation?
Standard retry automation treats all failures equally, attempting to brute-force recovery on a fixed schedule. RecoverOS models recovery as an economic optimization problem. It uses AI to determine the probability of success based on root-cause analysis, and calculates an Expected Recovery Value (ERV) that accounts for execution costs and customer friction. This prevents wasting money on unrecoverable failures (like fraud) and optimizes net revenue.

## 2. Why does this need AI?
Traditional rules engines break down when dealing with unstructured or highly variable bank failure codes across different payment gateways. AI (specifically LLMs) excels at semantic mapping—taking varied textual error descriptions and customer contexts and collapsing them into standardized failure categories and probability distributions. 

## 3. What does the AI actually do?
The AI acts strictly as a **Diagnosis Engine**. Given the webhook payload, customer history, and failure description, it outputs a strict JSON schema containing:
1. A standardized failure category (e.g., `insufficient_funds`).
2. A confidence score.
3. Probability estimations for specific intervention types (e.g., probability that a payment link will succeed vs a silent retry).

## 4. Can AI accidentally charge a customer?
**No.** This is a hard architectural boundary. The AI has absolutely zero execution authority. It cannot call APIs, and it cannot mutate the database. It solely emits probabilities. The deterministic `DecisionEngine` uses those probabilities to calculate ERV, and the `GuardrailEngine` applies hard business rules. Only the `ActionExecutor` (standard Python code) interacts with Razorpay APIs.

## 5. What happens when Razorpay times out?
The `RazorpayAdapter` handles timeouts explicitly. If a timeout occurs during payment link creation, the `ActionExecutor` safely marks the execution as `EXECUTION_UNKNOWN` or fails gracefully, avoiding state corruption. 

## 6. What happens when two workers receive the same webhook?
RecoverOS implements atomic idempotency at the database layer. The `webhook_events` table has a `UNIQUE` constraint on `webhook_event_id`. When a webhook arrives, the system attempts to insert it with a status of `RESERVED`. If another worker attempts this simultaneously, the database raises an `IntegrityError`, and the duplicate is dropped.

## 7. What happens when payment arrives after STOPPED?
If a customer pays a link after the journey was marked `STOPPED` or `EXHAUSTED`, the `ReconciliationService` still processes the `payment_link.paid` webhook. It associates the payment with the original journey, updates the recovered amount, and transitions the status to `RECOVERED`. The system is designed for eventually consistent reality.

## 8. What happens when the LLM goes down?
The `DecisionEngine` gracefully degrades. If the LLM provider times out or returns an error, the system falls back to a deterministic `MockDiagnosisProvider` or applies safe defaults (e.g., marking the failure as `unknown` and relying purely on safe guardrails). Money movement is never blocked indefinitely by an AI outage.

## 9. What happens when the LLM gives garbage?
The provider integration relies on strictly typed Pydantic validation. If the LLM returns malformed JSON or violates the schema, the parsing fails immediately. The system treats this identically to a network outage and falls back to deterministic safe defaults.

## 10. Can the system double-create payment links?
No. The `GuardrailEngine` evaluates historical actions. If a `recovery_link` was already created for a specific journey round and hasn't expired, the `DUPLICATE_ACTION` guardrail prevents the engine from generating a second one.

## 11. How do you measure business value?
We measure Net Recovered Value. We sum the total money successfully reconciled via settlement webhooks, and subtract the direct API execution costs (e.g., SMS fees for payment links) and the estimated friction costs.

## 12. Are the benchmark numbers real?
**No.** The benchmark in `evaluate_batch.py` operates on synthetic, not production-observed data. The evaluation uses a hidden ground-truth model based on documented assumptions, meaning the AI probabilities do not generate the outcomes. The baseline and RecoverOS policies face identical scenario populations, and deterministic seeds make reruns fully reproducible. Additionally, sensitivity analysis rigorously tests the underlying economic assumptions. Ultimately, the benchmark demonstrates relative policy performance under the simulator, not guaranteed real-world revenue.

## 13. What is genuinely live?
When `RAZORPAY_LIVE_EXECUTION=true` with valid test credentials (`rzp_test_...`), RecoverOS communicates with the live Razorpay Test Mode REST API. It creates actual hosted Payment Links (`POST /v1/payment_links` on `api.razorpay.com`), cancels links (`POST /v1/payment_links/{id}/cancel`), processes incoming Razorpay webhooks, and verifies HMAC-SHA256 signatures exactly as in production.

## 14. What is simulated vs recommendation-only?
1. **Recommendation-Only:** `payment_method_update` generates customer guidance and simulated update session tokens because the public Razorpay test sandbox does not provide direct mandate update API endpoints.
2. **Local Simulation:** `retry_now` (immediate re-auth), `retry_later` (delayed dunning queue), `send_reminder` (customer notifications), and `escalate_human` (CRM concierge dispatch) are executed via internal state machine transitions and local test clients.
3. **Settlement Simulation:** Customer paying the payment link is simulated by triggering a signed `payment_link.paid` webhook, demonstrating the closed-loop reconciliation flow without manual card entry during automated runs.

## 15. Why is RecoverOS better than a simple rules engine?
A simple rules engine cannot easily factor in continuous variables like Customer Lifetime Value, nor can it dynamically adjust to multi-dimensional costs. ERV allows RecoverOS to adapt: a high-friction action might be rejected for a ₹50 payment, but approved for a ₹50,000 payment, without writing thousands of nested `IF` statements.

## 16. What is the counterfactual advantage?
It is the quantified proof of value. Whenever the system makes a decision, it simulates what the "Next Best Action" (the fallback) would have been. The economic difference between the chosen action's ERV and the fallback's ERV is the Counterfactual Advantage.

## 17. What happens when recovery costs more than the amount recovered?
The `NEGATIVE_ERV_PROTECTION` guardrail blocks the action. If a ₹5 transaction fails, and sending an SMS costs ₹1.50 with a high risk of customer churn (friction ₹10), the ERV is negative. The system will cleanly drop the recovery attempt (choose `stop`) to prevent net loss.

## 18. How does the merchant understand the decision?
The Merchant Command Center (UI) is built for observability. The Journey Investigation page explicitly breaks down the AI Diagnosis, lists every candidate action with its calculated ERV, shows which guardrails were triggered, and highlights the counterfactual proof.

## 19. Why should Razorpay care about this?
Razorpay currently offers standard retry logic. By offering an economic, AI-driven recovery control plane, Razorpay could help large enterprise merchants recover millions in lost SaaS revenue while minimizing customer churn, creating a massive upsell product opportunity.

## 20. Why should Razorpay hire the builder?
RecoverOS demonstrates exactly what is required to build safe fintech products in the AI era. It prioritizes atomic idempotency, closed-loop reconciliation, strict schema validation, deterministic guardrails, and adversarial testing over generic LLM API wrappers. It proves an understanding of how money actually moves safely.
