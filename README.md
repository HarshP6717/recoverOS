# RecoverOS — Phase 1: Data & Simulation Foundation

**RecoverOS** is an AI-powered revenue recovery control plane for failed subscription payments.

This repository contains the **Phase 1 Data and Simulation Foundation**, providing:
- A reproducible, realistic 10,000-record synthetic payment failure dataset.
- A ground-truth action-specific recovery simulation engine modeled in **Indian Rupees (INR ₹)**.
- Deterministic guardrails and policy action selectors (Baseline vs. RecoverOS ML vs. Theoretical Oracle).
- An interpretable Scikit-Learn Machine Learning pipeline (Calibrated Logistic Regression).
- A counterfactual evaluation harness that rigorously separates ML policy decisions from ground-truth simulated outcomes.

---

## 1. Why Synthetic Data is Being Used

Payment failure and recovery datasets in production systems contain highly sensitive Personally Identifiable Information (PII) and proprietary financial records (customer names, PAN, card numbers, transaction history, billing identifiers, and financial institution routing data).

Synthetic data generation is used in this foundation phase to:
1. **Enable Strict Reproducibility:** Ensure controlled benchmarks with fixed random seeds (`seed=42`) across data splits and policy simulations.
2. **Model Ground-Truth Counterfactuals:** In historical production logs, we only observe the outcome of the single action that was taken. Synthetic simulation allows us to define the true underlying conditional recovery probability distribution $P^*(\text{recovery} \mid X, A)$ across all 7 candidate actions for counterfactual policy evaluation.
3. **Simulate Realistic Edge Cases & Failure Modes:** Systematically generate diverse failure patterns (e.g. UPI timeouts, mandate returns, expired cards, 3DS abandonments) that occur at varying frequencies in real subscription businesses.
4. **Protect User Privacy:** Develop and validate ML recovery algorithms without risking real customer financial data.

---

## 2. Dataset Schema & Split

The dataset contains exactly **10,000 payment failure records** partitioned into:
- **Training Set (`train.csv`):** 8,000 records
- **Validation Set (`val.csv`):** 1,000 records
- **Held-Out Test Set (`test.csv`):** 1,000 records (strictly isolated from model tuning and feature engineering)

### Feature Schema

| Feature Name | Data Type | Description |
| :--- | :--- | :--- |
| `transaction_id` | `str` | Unique transaction identifier (`tx_00000` – `tx_09999`) |
| `customer_id` | `str` | Customer account identifier (`cust_00000` – `cust_03999`) |
| `subscription_id` | `str` | Subscription identifier (`sub_00000` – `sub_05499`) |
| `amount` | `float` | Invoice amount at risk in **INR (₹)** (e.g., ₹199 to ₹9,999) |
| `payment_method` | `str` | Payment method: `upi`, `card`, `netbanking`, `mandate_nach`, `wallet` |
| `failure_type` | `str` | Categorized decline reason (9 failure types) |
| `attempt_number` | `int` | Current retry attempt index (1 to 5) |
| `days_overdue` | `int` | Days elapsed since initial payment failure (0 to 45) |
| `previous_payment_count` | `int` | Total historical billing cycles (1 to 36) |
| `previous_success_count` | `int` | Historical successful payment count ($\le$ `previous_payment_count`) |
| `previous_failure_count` | `int` | Historical failed payment count (`previous_payment_count - previous_success_count`) |
| `previous_recovery_count` | `int` | Historical recovered payment count ($\le$ `previous_failure_count`) |
| `previous_success_rate` | `float` | Success ratio: `previous_success_count / previous_payment_count` |
| `previous_recovery_rate` | `float` | Recovery ratio: `previous_recovery_count / max(1, previous_failure_count)` |
| `customer_lifetime_value`| `float` | Cumulative customer lifetime value in **INR (₹)** |
| `contact_count` | `int` | Number of previous recovery communications sent (0 to 8) |
| `subscription_age_days` | `int` | Subscription age in days since inception |
| `action` | `str` | Historical recovery action applied |
| `recovery_probability` | `float` | True ground-truth recovery probability under assigned action |
| `action_cost` | `float` | Synthetic execution cost in **INR (₹)** |
| `expected_recovery_value`| `float` | Expected Recovery Value ($\text{amount} \times P - \text{cost}$) in **INR (₹)** |
| `recovered` | `bool` | Binary ground-truth simulated outcome (`True` / `False`) |
| `recovered_amount` | `float` | Recovered revenue (`amount` if recovered else ₹0.00) in **INR (₹)** |
| `net_value` | `float` | Net financial recovery (`recovered_amount - action_cost`) in **INR (₹)** |

---

## 3. Recovery Actions & Synthetic Cost Structure

RecoverOS supports 7 distinct recovery actions.

> [!IMPORTANT]
> **Synthetic Cost Assumptions:** The action execution costs listed below are synthetic simulation assumptions in **INR (₹)** designed for economic modeling and policy optimization. They are **not** actual Razorpay gateway fees.

| Recovery Action | Synthetic Cost (₹) | Operational Description |
| :--- | :--- | :--- |
| `retry_now` | ₹1.00 | Immediate payment re-attempt via payment gateway API. |
| `retry_later` | ₹1.00 | Scheduled re-attempt timed after optimal delay (e.g. 24–72 hours). |
| `send_reminder` | ₹0.50 | Automated notification (WhatsApp / SMS / Email) prompting payment. |
| `payment_method_update` | ₹2.00 | Interactive workflow prompting customer to replace expired/declined card or mandate. |
| `recovery_link` | ₹1.50 | Hosted payment checkout link sent directly to customer via messaging. |
| `escalate_human` | ₹30.00 | Manual intervention by high-touch account representative or concierge. |
| `stop` | ₹0.00 | Cease recovery actions to prevent wasted spend and customer annoyance. |

---

## 4. Ground-Truth Simulation Logic

The ground-truth simulation engine in [`simulator/recovery_simulator.py`](file:///d:/internship/recoveros/simulator/recovery_simulator.py) calculates the conditional recovery probability $P^*(\text{recovery} \mid X, A)$ using domain-realistic affinity matrices and contextual log-odds adjustments:

$$\text{logit} = \text{logit}_{\text{base}}(\text{failure\_type}, \text{action}) + \Delta_{\text{method}} + \Delta_{\text{history}} + \Delta_{\text{attempt}} + \Delta_{\text{overdue}} + \Delta_{\text{fatigue}} + \Delta_{\text{clv}}$$

$$P^*(\text{recovery} \mid X, A) = \frac{1}{1 + e^{-\text{logit}}}$$

### Key Dynamics:
1. **Failure Suitability:**
   - `bank_timeout`: High response to `retry_now` (~0.74) and `retry_later` (~0.79).
   - `insufficient_funds`: `retry_now` fails (~0.08); `retry_later` succeeds (~0.68) after funds replenish.
   - `expired_card` / `invalid_payment_method`: Retries are futile (~0.01); `payment_method_update` (~0.82) and `recovery_link` (~0.74) succeed.
   - `hard_decline`: Stolen/blocked card; requires `payment_method_update` (~0.70) or `escalate_human` (~0.66).
   - `customer_abandoned`: Dropouts during checkout/3DS respond best to `recovery_link` (~0.68) and `send_reminder` (~0.52).
2. **Contextual Penalties & Boosts:**
   - **Attempt Decay:** Subsequent attempts diminish probability by $-0.32 \times (\text{attempt} - 1)$ in logit space.
   - **Overdue Decay:** Aging debt drops likelihood by $-0.035 \times \text{days\_overdue}$.
   - **Communication Fatigue:** Excess contacts ($\ge 3$) reduce response likelihood by $-0.12$ per contact.
   - **Customer Loyalty:** Long-tenure, high CLV customers receive a responsiveness boost.
3. **Expected Recovery Value (ERV):**
   $$\text{ERV} = \text{amount} \times P^*(\text{recovery} \mid X, A) - \text{action\_cost}$$
4. **Bernoulli Outcome Sampling:**
   For action execution, outcome `recovered` $\sim \text{Bernoulli}(P^*)$.

---

## 5. Recovery Policies

### 1. Deterministic Baseline Policy
A fixed rule-based dunning strategy representing legacy subscription systems:
- **Attempt 1:** `payment_method_update` if hard failure (`expired_card`, `hard_decline`, `invalid_payment_method`), else `retry_now`.
- **Attempt 2:** `recovery_link` if hard failure, else `retry_later`.
- **Attempt 3:** `send_reminder`.
- **Attempt 4+:** `stop`.

### 2. RecoverOS ML Expected Value Policy
Evaluates all candidate actions for a given invoice using the trained ML model:
1. Predicts $\hat{P}(\text{recovery} \mid X, A)$ for all candidate actions.
2. Computes predicted $\widehat{\text{ERV}} = \text{amount} \times \hat{P} - \text{action\_cost}$.
3. Enforces **Deterministic Safety Guardrails**:
   - **Guardrail 1 (Negative ROI suppression):** If $\max \widehat{\text{ERV}} \le 0$, select `stop`.
   - **Guardrail 2 (Permanent failure suppression):** Suppress `retry_now` and `retry_later` on permanent failures (`hard_decline`, `expired_card`, `invalid_payment_method`).
   - **Guardrail 3 (Micro-amount human escalation suppression):** Suppress `escalate_human` (₹30.00) if invoice amount $< ₹100.00$ to prevent heavy margin erosion.
4. Selects the permitted action with the highest predicted $\widehat{\text{ERV}}$.

### 3. Ground-Truth Oracle — Theoretical Upper Bound
Directly calculates ground-truth ERV using unobservable true probabilities and selects $\arg\max_A \text{ERV}_{\text{true}}$.
> **Note:** The Oracle represents the theoretical upper bound ceiling and is **not** available to production systems.

---

## 6. Machine Learning Model & Preprocessing

- **Architecture:** Calibrated `LogisticRegression` with L2 regularization (`C=1.0`) and 5-fold sigmoid probability calibration (`CalibratedClassifierCV`).
- **Feature Engineering & Preprocessing:**
  - **Numerical Features:** `amount`, `attempt_number`, `days_overdue`, `previous_payment_count`, `previous_success_count`, `previous_failure_count`, `previous_recovery_count`, `customer_lifetime_value`, `contact_count`, `subscription_age_days`, `previous_success_rate`, `previous_recovery_rate` standardized using `StandardScaler`.
  - **Categorical Features:** `payment_method`, `failure_type`, `action` one-hot encoded using `OneHotEncoder(handle_unknown='ignore')`.
  - **Interaction Features:** `failure_action_interaction` (`failure_type + '__' + action`) and `method_action_interaction` (`payment_method + '__' + action`) to capture action-specific decline dynamics.
- **Model Artifact:** Serialized to [`ml/models/recovery_model.joblib`](file:///d:/internship/recoveros/ml/models/recovery_model.joblib).

---

## 7. Counterfactual Evaluation & Benchmark Results

The evaluation pipeline rigorously separates ML prediction from outcome generation:
- The ML model predicts probabilities to make policy decisions.
- The **independent Ground-Truth Simulator** samples the actual outcome via Bernoulli trials using fixed seeds.

### 1. Statistical ML Model Performance (Held-Out Test Set: 1,000 cases)

| ML Metric | Test Set Score | Interpretation |
| :--- | :--- | :--- |
| **ROC-AUC** | **0.8767** | High discriminative ability across recovery outcomes. |
| **PR-AUC** | **0.7956** | Precision-Recall curve indicates reliable positive class detection. |
| **Log Loss** | **0.4284** | Well-minimized cross-entropy penalty. |
| **Brier Score** | **0.1384** | Mean squared probability error demonstrates accuracy. |
| **Expected Calibration Error (ECE)** | **0.0308** | Predicted probabilities deviate by $\le 3.1\%$ from observed frequencies. |

---

### 2. Policy Financial Comparison (1,000 Held-Out Test Cases, Invoices at Risk: ₹1,155,106.52)

| Policy Strategy | Gross Recovered (₹) | Execution Costs (₹) | Net Value Recovered (₹) | Net Recovery Rate (%) | Invoices Resolved (%) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Deterministic Baseline** | ₹487,420.63 | ₹980.50 | ₹486,440.13 | **42.11%** | 40.10% |
| **RecoverOS ML Policy** | ₹824,730.19 | ₹1,939.00 | **₹822,791.19** | **71.23%** | **69.50%** |
| **Ground-Truth Oracle (Ceiling)** | ₹832,613.62 | ₹2,975.50 | ₹829,638.12 | **71.82%** | 70.10% |

### Key Policy Observations:
- **Net Revenue Lift:** RecoverOS recovers an additional **₹336,351.06** in net revenue (+69.1% net recovery lift over the deterministic baseline).
- **Proximity to Oracle:** RecoverOS achieves **71.23%** net recovery, approaching within **0.59%** of the theoretical Oracle upper bound (71.82%).
- **Efficiency Mechanism:** The baseline wastes 459 attempts on immediate retries (`retry_now`) on transient liquidity or credential failures where immediate retries fail. RecoverOS shifts these to timed retries (`retry_later`: 605) and targeted customer links (`recovery_link`: 200, `payment_method_update`: 172).

### 3. Action Distribution Breakdown (1,000 Test Cases)

| Policy | `retry_now` | `retry_later` | `send_reminder` | `payment_method_update` | `recovery_link` | `escalate_human` | `stop` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Deterministic Baseline** | 459 | 221 | 117 | 88 | 44 | 0 | 71 |
| **RecoverOS ML Policy** | 0 | 605 | 0 | 172 | 200 | 23 | 0 |
| **Ground-Truth Oracle** | 0 | 609 | 0 | 197 | 135 | 59 | 0 |

---

## 8. Explicit Disclaimer & Limitations

> [!CAUTION]
> **Synthetic Data Disclaimer:**
> The dataset, action costs, recovery probabilities, and simulated outcomes generated in this repository are **strictly synthetic mathematical models** created for software architecture, algorithmic design, and simulation benchmarking.
> - They do **not** represent real-world payment statistics from any specific merchant, bank, or payment gateway.
> - Action costs (e.g. ₹1.00 for retries, ₹30.00 for human escalation) are synthetic simulation assumptions, not Razorpay or banking charges.
> - Ground-truth probability matrices are illustrative and must be re-calibrated on real historical merchant logs before live production deployment.

---

## 9. How to Run & Reproduce

### Prerequisites
- Python 3.11+
- Virtual environment recommended

### Installation
```powershell
pip install -r requirements.txt
```

### 1. Generate Synthetic Dataset (10,000 records)
```powershell
python ml/synthetic_generator.py
```
Outputs:
- `data/raw/payment_failures_10k.csv` (10,000 records)
- `data/processed/train.csv` (8,000 records)
- `data/processed/val.csv` (1,000 records)
- `data/processed/test.csv` (1,000 records)

### 2. Train ML Model
```powershell
python ml/train.py
```
Outputs:
- Validation metrics
- Saved model: `ml/models/recovery_model.joblib`

### 3. Run Full Evaluation & Policy Benchmark
```powershell
python ml/evaluate.py
```

### 4. Run Automated Test Suite
```powershell
pytest -v tests/
```
