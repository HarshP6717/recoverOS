# RecoverOS Phase 3 — Architecture & Data Flow Specification

**Document Version:** 1.0.0  
**Target:** Razorpay AI Buildathon — Track 03: AI Revenue Recovery  
**Focus:** Architectural layout, data pipeline, component boundaries, and security design.

---

## 1. End-to-End System Architecture

```mermaid
flowchart TD
    subgraph Razorpay_Platform["Razorpay Ecosystem (Test Mode)"]
        RZP_WH["Razorpay Webhook Stream<br/>(payment.failed, payment_link.paid, subscription.charged)"]
        RZP_PLINK["Razorpay Payment Links API<br/>(POST /v1/payment_links)"]
        RZP_CHECKOUT["Razorpay Hosted Checkout<br/>(https://rzp.io/i/...)"]
        RZP_CANCEL["Razorpay Cancel Link API<br/>(POST /v1/payment_links/{id}/cancel)"]
    end

    subgraph Ingestion_Layer["1. Ingestion & Security Gateway"]
        WH_GATEWAY["Webhook Handler (/v1/webhooks/razorpay)"]
        HMAC_VERIFY{"HMAC-SHA256<br/>Signature Valid?"}
        IDEMP_CHECK{"Atomic Idempotency<br/>(UNIQUE webhook_event_id)"}
        NORM_SERVICE["Event Normalizer & Failure Classifier"]
    end

    subgraph State_Control["2. Stateful Journey Control Plane"]
        JOURNEY_ENGINE["Recovery Journey State Machine<br/>(Round 1 -> Round 2 -> Round 3)"]
        RECON_ENGINE["Closed-Loop Reconciliation Engine"]
    end

    subgraph Intelligence_Core["3. AI Intelligence & Safety Core"]
        DECISION_ENGINE["RecoverOS Decision Engine"]
        ML_MODEL["Calibrated Logistic Regression<br/>(Expected Value Maximizer)"]
        GUARDRAILS{"Deterministic Safety Guardrails<br/>(Hard Failure & Micro-Invoice Caps)"}
        HEURISTIC_FALLBACK["Strong Feature-Aware Heuristic<br/>(Zero-Downtime Fallback)"]
    end

    subgraph Execution_Layer["4. Hybrid Action Execution Layer"]
        ACTION_DISPATCHER["Hybrid Action Executor"]
        TIER_A["Tier A: Razorpay API Dispatcher<br/>(payment_links, cancellation)"]
        TIER_BC["Tier B/C: Simulated Gateway / Queue<br/>(retry_later, escalate_human, stop)"]
    end

    subgraph Persistence_Layer["5. Ledger & Persistence Layer"]
        DB_JOURNEYS[("recovery_journeys Table")]
        DB_EVENTS[("recovery_events Audit Ledger")]
        DB_WEBHOOKS[("processed_webhooks Table")]
    end

    subgraph Presentation_Layer["6. Live Operator & Judge UI"]
        DASHBOARD["RecoverOS Live Control Console<br/>(Revenue at Risk, Decision Diagnosis, Timeline)"]
    end

    %% Data Flow Connections
    RZP_WH -->|HTTP POST| WH_GATEWAY
    WH_GATEWAY --> HMAC_VERIFY
    HMAC_VERIFY -- No --> Reject401["HTTP 401 Unauthorized"]
    HMAC_VERIFY -- Yes --> IDEMP_CHECK
    IDEMP_CHECK -- Duplicate --> Ignore200["HTTP 200 Duplicate Acknowledged"]
    IDEMP_CHECK -- New Event --> NORM_SERVICE

    NORM_SERVICE -->|Failure Event| JOURNEY_ENGINE
    NORM_SERVICE -->|Success Event| RECON_ENGINE

    RECON_ENGINE -->|Mark RECOVERED| DB_JOURNEYS
    RECON_ENGINE -->|Cancel Open Link| RZP_CANCEL
    RECON_ENGINE -->|Log Financial Gain| DB_EVENTS

    JOURNEY_ENGINE --> DECISION_ENGINE
    DECISION_ENGINE --> ML_MODEL
    ML_MODEL --> GUARDRAILS
    DECISION_ENGINE -. If Model Error .-> HEURISTIC_FALLBACK

    GUARDRAILS --> ACTION_DISPATCHER
    ACTION_DISPATCHER --> TIER_A
    ACTION_DISPATCHER --> TIER_BC

    TIER_A -->|Create Hosted Link| RZP_PLINK
    RZP_PLINK -->|Hosted URL| RZP_CHECKOUT
    RZP_CHECKOUT -. Customer Pays .-> RZP_WH

    ACTION_DISPATCHER --> DB_EVENTS
    JOURNEY_ENGINE --> DB_JOURNEYS
    IDEMP_CHECK --> DB_WEBHOOKS

    DB_JOURNEYS -. Live Stream .-> DASHBOARD
    DB_EVENTS -. Live Stream .-> DASHBOARD
```

---

## 2. Component Breakdown: Existing vs. To Be Built

| Component | Status | Location in Codebase | Phase 3 Enhancement / Role |
|---|---|---|---|
| **HMAC Signature Verifier** | Existing | `backend/app/services/razorpay_adapter.py` | Verify inbound Razorpay payloads with secret key. |
| **Atomic Idempotency** | Existing | `backend/app/models/database.py` | Prevent duplicate webhook processing via unique constraints. |
| **Error Code Normalizer** | Existing | `backend/app/services/razorpay_adapter.py` | Maps gateway error codes to 9 normalized failure categories. |
| **ML Decision Engine** | Existing | `backend/app/services/decision_engine.py` | Evaluates 7 candidate actions via calibrated ERV. |
| **Deterministic Guardrails** | Existing | `backend/app/services/guardrails.py` | Enforces hard failure retry suppression & micro-invoice caps. |
| **Feature-Aware Heuristic** | Existing | `evaluation/policies/feature_aware_heuristic.py` | Embed as zero-downtime fallback when model inference is degraded. |
| **Stateful Journey Engine** | **To Be Built** | `backend/app/services/journey_service.py` | Manage multi-round state (R1 -> R2 -> R3) in the database. |
| **Closed-Loop Reconciler** | **To Be Built** | `backend/app/services/reconciliation_service.py` | Auto-match settlement webhooks, cancel open links, mark recovered. |
| **Live Payment Links Client** | Existing / Upgrade | `backend/app/services/razorpay_client.py` | Connect to live Razorpay Test Mode REST API endpoints. |
| **Live Operator Console UI** | **To Be Built** | `frontend/` or standalone live dashboard | Provide clean visual timeline of revenue at risk, decisions, and audit trail. |

---

## 3. Reliability, Safety & Failure Modes

1. **Zero-Downtime Heuristic Fallback:**
   If the ML model artifact fails to load, times out, or encounters unexpected missing features, the Decision Engine automatically falls back to the **Strong Feature-Aware Heuristic**, ensuring recovery decisions are never dropped or blocked.
2. **Razorpay Gateway Timeout Handling:**
   If Razorpay's API encounters a 504 Gateway Timeout or 503 Unavailable during payment link creation, the action is marked `EXECUTION_PENDING_RETRY` in the ledger and queued for safe retry without duplicating state.
3. **Double-Payment Prevention:**
   When a recovery event is confirmed via `payment.captured` or `payment_link.paid`, the Closed-Loop Reconciler immediately issues a `POST /v1/payment_links/{id}/cancel` call for any open links associated with that transaction.
4. **Data Privacy & Identifier Protection:**
   Transaction and customer identifiers are hashed in client-facing logs. No raw card numbers or CVVs ever touch RecoverOS servers (all checkout interaction occurs on Razorpay's PCI-DSS compliant hosted pages).
