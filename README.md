# RecoverOS: Economic AI for Payment Recovery

📹 Pitch video: [link coming soon]

**RecoverOS is a Razorpay-native payment recovery control plane.** It shifts revenue recovery from blind retry automation to **economic decision-making**.

Traditional recovery logic is rules-based: *If a payment fails, retry it 3 times and stop.*
RecoverOS introduces an intelligent layer: *If a payment fails, diagnose the root cause with AI, calculate the Expected Recovery Value (ERV) of all possible interventions (accounting for direct cost and customer friction), apply deterministic guardrails, and execute the optimal action safely via Razorpay.*

### Why Track 03 — AI Revenue Recovery?
RecoverOS was engineered specifically to meet the evaluation bar of **Track 03 (AI Revenue Recovery)**: replacing dumb, fixed-cadence retries with an autonomous, economically rational recovery system. We align directly with the track's core requirements by delivering **measured recovery** (friction-adjusted Expected Recovery Value maximization), **compliant escalation** (graceful handoff to human concierges when diagnosis uncertainty is high), explicit **stopping rules** (hard caps on customer dunning fatigue and micro-amount losses), and an end-to-end **audit trail** (transparent SQLite event sourcing recording every diagnosis, guardrail evaluation, and counterfactual comparison).

---

## ⚡ 60-Second Overview

1. **The Problem:** Blind retry logic treats all failures equally, wasting money on unrecoverable transactions (e.g., fraud) and burning customer goodwill with high friction.
2. **The Solution:** RecoverOS evaluates every failure using an **Economic Decision Engine**. It determines if a failure should be retried, escalated to a human, or recovered via a Razorpay Payment Link, optimizing for net revenue.
3. **The AI Boundary (Safe AI):** **The AI has ZERO execution authority.** It solely acts as a Diagnosis Engine (identifying root causes and estimating probabilities). A deterministic, auditable pipeline enforces safety and makes the final decision.
4. **Counterfactual Advantage:** The system proves its worth by simulating the "Counterfactual Advantage"—showing exactly how much extra net revenue the AI-driven ERV calculation generated compared to the next best standard action.
5. **Real Razorpay Integration:** The system operates fully closed-loop with Razorpay Test Mode, issuing live Webhooks, generating Payment Links, and reconciling settlements atomically. By default this runs with `AI_PROVIDER=mock` and `RAZORPAY_LIVE_EXECUTION=false` for deterministic, reproducible demos. Set `AI_PROVIDER=gemini` (+ `GEMINI_API_KEY`) and `RAZORPAY_LIVE_EXECUTION=true` (+ real `rzp_test_` credentials) to run against the live Gemini and Razorpay Test Mode APIs — see [LIVE_MODE.md](LIVE_MODE.md).

## 🚀 How to Run the Golden Demo

RecoverOS includes a deterministic end-to-end golden demo that simulates a Razorpay failure webhook, routes it through the AI Diagnosis and Decision Engine, executes a Razorpay Test Mode payment link, simulates the customer paying it, and reconciles the settlement.

```bash
# 1. Start the control plane
uvicorn backend.app.main:app --reload

# 2. In another terminal, run the Golden E2E Demo
python backend/scripts/golden_demo.py
```
> See [DEMO.md](DEMO.md) for full instructions on viewing the Merchant Command Center UI, and [LIVE_MODE.md](LIVE_MODE.md) for running against live gateway APIs.

## 🧠 Architecture Overview

RecoverOS is designed as a highly resilient, idempotent orchestration layer.
Read the full architecture in [ARCHITECTURE.md](ARCHITECTURE.md).

```text
Razorpay Webhook (payment.failed)
       ↓
Webhook Gateway (Idempotency, HMAC Verification)
       ↓
AI Diagnosis Engine (Gemini / Mock Provider)
       ↓
Economic Decision Engine (ERV = (Amount * Probability) - Cost - Friction)
       ↓
Guardrail Engine (Hard Business Logic Overrides)
       ↓
Action Executor (Razorpay Payment Links / Retry Logic)
       ↓
Razorpay Settlement Webhook
       ↓
Reconciliation Service
```

## 🔌 Razorpay Execution Truth

RecoverOS explicitly separates live gateway actions from recommendation-only and simulated actions:
- **Live Razorpay Test Mode REST API (enabled via `RAZORPAY_LIVE_EXECUTION=true`):** `recovery_link` (creates genuine hosted links on `api.razorpay.com`), `stop` / link cancellation (`/v1/payment_links/{id}/cancel`), and HMAC-verified Webhook ingestion (`payment.failed`, `payment_link.paid`).
- **Recommendation-Only Capability:** `payment_method_update` (generates customer update session URL and guidance; public Razorpay sandbox does not offer a direct mandate replacement session endpoint, so RecoverOS treats it as an actionable recommendation).
- **Internal / Queue Simulation:** `retry_now` (immediate re-auth simulation), `retry_later` (delayed queue scheduling), `send_reminder` (notification dispatch), and `escalate_human` (CRM concierge dispatch).

See [ARCHITECTURE.md](ARCHITECTURE.md) (Section 5) for the complete classification matrix and [LIVE_MODE.md](LIVE_MODE.md) for live execution instructions.

## 📊 Benchmark Credibility (Deterministic Synthetic Evaluation)

RecoverOS includes a **Deterministic Synthetic Evaluation** (`python backend/scripts/evaluate_batch.py`) of 1,000 scenarios evaluated against an isolated hidden ground-truth outcome model, saved in the test-proof frozen reference file [`evaluation/results/benchmark_1000_seed42.json`](evaluation/results/benchmark_1000_seed42.json) (and mirrored to `evaluation/results/latest.json`). Recovery outcomes are decoupled from RecoverOS's predicted probabilities, and both policies are evaluated on the exact same scenario population using a fixed random seed (42).

> **Note on Benchmark vs. Exploratory Artifacts:** `backend/scripts/evaluate_batch.py` (executing the production `DecisionEngine` + deterministic guardrails) is the canonical benchmark suite. The `evaluation/` and `ml/` directories (such as `train.py`, `recovery_model.joblib`, `evaluator.py`) contain earlier/offline explorations and are not used by the live production decision path.

**Frozen Evaluation Results ([`evaluation/results/benchmark_1000_seed42.json`](evaluation/results/benchmark_1000_seed42.json)):**
- **Baseline (3x Static Retry):** Net Value ₹417,179.34 (Recovered: ₹419,399.34 | Direct Cost: ₹740.00 | Friction Cost: ₹1,480.00 | Recovery Rate: 16.9%)
- **RecoverOS (ERV v2 Friction-Adjusted):** Net Value ₹865,400.69 (Recovered: ₹872,814.69 | Direct Cost: ₹2,852.00 | Friction Cost: ₹4,562.00 | Recovery Rate: 35.2%)
- **Net Value Advantage:** **+₹448,221.35** (+107.4% net revenue improvement)

> **Assumptions & Limitations:** This benchmark is a deterministic synthetic evaluation based on documented simulation assumptions, fixed seed 42, and synthetic cost parameters in INR (₹). It is designed for controlled algorithmic comparison and is not evidence of production Razorpay recovery performance.

## 🔧 What Broke & How We Fixed It

During adversarial testing, I discovered a subtle contradiction between two independent guardrail checks in `backend/app/services/guardrails.py` and `backend/app/services/decision_engine.py`.

### The Defect: Guardrail Contradiction on Human Escalation
We designed human escalation (`escalate_human`) as an essential safety valve: when AI diagnosis confidence is low (< 0.60), the system should drop automated actions and escalate to a human agent.

However, the two guardrail layers disagreed on negative Expected Recovery Value (ERV) suppression:
1. `guardrails.py` implemented Guardrail G1 to suppress non-viable actions (`ERV <= 0`), but explicitly exempted `escalate_human` (`elif erv <= 0.0 and action != "escalate_human"`) so low-confidence cases could still reach human oversight.
2. `decision_engine.py` contained an independent secondary loop that wiped out any candidate with `predicted_erv <= 0.0` without exempting `escalate_human`.

**The Failure Mode:** In adversarial scenarios with low AI confidence and modest transaction amounts (for example, invoice amount ₹200.00 where `escalate_human` direct cost is ₹30.00 and predicted ERV is -₹20.00), the AI correctly flagged `LOW_AI_CONFIDENCE`. But because the raw ERV was negative, `decision_engine.py` suppressed `escalate_human`. With all recovery actions suppressed, the engine silently selected `stop` instead of escalating to a human — the exact opposite of our intended safety behavior.

### How We Fixed It
1. **Single Source of Truth:** We made `backend/app/services/guardrails.py` the single authority for guardrail enforcement and aligned `backend/app/services/decision_engine.py` to preserve `escalate_human` as an authorized safety bypass when raw ERV is negative.
2. **Deterministic Boundaries Kept:** We ensured Guardrail G3 remained strict: human escalation is suppressed for micro-amounts (< ₹100.00) where concierge cost cannot be justified, but permitted for amounts ≥ ₹100.00 even if predicted ERV is negative under low AI confidence.
3. **Regression Test Lock-in:** We locked in the fix with the regression test [`test_low_ai_confidence_escalates_human_even_with_negative_erv`](backend/tests/test_guardrails.py) in `backend/tests/test_guardrails.py`, verifying that adversarial low-confidence inputs correctly output `selected_action: "escalate_human"` with `LOW_AI_CONFIDENCE` triggered and `G1_NEGATIVE_ERV` suppressed.

## 🛡️ Engineering Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Deep dive into orchestrator, idempotency, AI boundary, and Razorpay capabilities.
- [DECISIONS.md](DECISIONS.md) - Architectural Decision Records (ADRs) explaining key engineering choices.
- [DEMO.md](DEMO.md) - Exact step-by-step golden demo and Merchant UI walkthrough guide.
- [FAQ.md](FAQ.md) - Technical and architectural deep-dive Q&A.
- [LIVE_MODE.md](LIVE_MODE.md) - Guide and verification for live Gemini and Razorpay Test Mode APIs.
- [PITCH.md](PITCH.md) - 5-minute final pitch presentation script.

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

