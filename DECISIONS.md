# Architectural Decision Records (ADRs)

This document tracks the major engineering and product decisions made while building RecoverOS.

---

## ADR 001: Separation of AI Diagnosis and Execution Authority

**Context:** We need to use AI to intelligently route payment recovery. Modern patterns often lean towards "Agents" that use function calling to execute API actions dynamically.

**Decision:** We strictly prohibited the AI from executing actions. The AI acts only as a Diagnosis Provider. It receives context and outputs failure categories and success probabilities via a strict JSON schema. A deterministic Economic Decision Engine consumes these probabilities.

**Rationale:** Financial systems cannot tolerate hallucination. If an agent hallucinates a loop, it could create thousands of Razorpay payment links, charging merchants for SMS/email costs, and spamming customers. By restricting the AI to probability estimation, we guarantee safety. The absolute worst-case scenario of an AI hallucination is a sub-optimal deterministic action choice, not an infinite API loop.

---

## ADR 002: Expected Recovery Value (ERV) vs Highest Probability

**Context:** Once we know the probability of success for an action, we need to choose which action to take. The naive approach is choosing the action with the highest probability.

**Decision:** We implemented the ERV calculation: `ERV = (Probability * Amount) - Direct Cost - Friction Cost`. We always choose the action with the highest ERV.

**Rationale:** A human escalation might have a 99% success rate but costs ₹30. A payment link might have an 85% success rate but costs ₹1.50. On a ₹50 transaction, the human escalation results in a net loss. ERV mathematically aligns the recovery strategy with the actual economic goals of the merchant. Customer friction is quantified and subtracted to prevent long-term churn.

---

## ADR 003: SQLite Atomic Idempotency

**Context:** Razorpay webhooks can be delivered multiple times (e.g., due to network timeouts). We must prevent processing the same webhook twice, which could lead to duplicate payment links.

**Decision:** We use SQLite's native `UNIQUE` constraint on `webhook_event_id` in a dedicated `webhook_events` table. The application attempts to insert the event *before* processing. If a constraint violation occurs, the event is safely dropped as a duplicate.

**Rationale:** Database-level uniqueness is the strongest guarantee against race conditions in distributed systems. It avoids complex Redis lock implementations while providing absolute correctness.

---

## ADR 004: Counterfactual Simulation

**Context:** In order to prove that RecoverOS is generating value, we need to show the delta between what it did, and what a "dumb" system would have done.

**Decision:** The Decision Engine executes a secondary pass during evaluation to determine the "Next Best Action" (the highest ERV action excluding the selected action). 

**Rationale:** This directly proves the financial impact of the AI/ERV layer. Without counterfactuals, a merchant (or judge) cannot visualize the money saved or the friction avoided. It acts as the primary visual proof of the system's intelligence on the Merchant Command Center.

---

## ADR 005: Read-Only Frontend

**Context:** The Merchant Command Center needs to display the system's status.

**Decision:** The frontend is strictly an observability and evidence layer. It does not implement business logic, and it does not allow manual mutation of the journey state.

**Rationale:** Re-implementing recovery logic in JavaScript introduces a split-brain problem. By keeping the frontend read-only, we ensure that the backend FastAPI application is the single source of truth, maximizing engineering credibility.
