# RecoverOS Phase 2C — Evaluation Report

**Generated:** 2026-08-28T07:15:29Z  
**Test Population:** 1000 held-out records (`data/processed/test.csv`)  
**Seed formula:** `seed = 42 + index`  
**Both policies evaluated on the exact same 1000 records in the same order.**

> All metrics are derived from actual simulated outcomes via `simulate_action()`.  
> No values are fabricated, estimated, or hard-coded.

---

## A. Measured Results Summary

| Metric | Baseline | RecoverOS | Absolute Diff | Relative Uplift |
|--------|----------|-----------|---------------|-----------------|
| Recovery Rate | 40.8000% | 70.5000% | 29.7000% | +72.7941% |
| Recovered Count | 408 | 705 | +297 | +72.7941% |
| Total Recovered Amount | ₹462,997.6800 | ₹843,254.5800 | ₹380,256.9000 | +82.1293% |
| Total Action Cost | ₹980.5000 | ₹1,939.0000 | ₹958.5000 | +97.7562% |
| Total Net Value | ₹462,017.1800 | ₹841,315.5800 | ₹379,298.4000 | +82.0962% |
| Avg Net Value / Case | ₹462.0172 | ₹841.3156 | ₹379.2984 | +82.0962% |
| Stop Rate | 7.1000% | 0.0000% | -7.1000% | -100.0000% |

---

## B. Baseline Policy Results

**Policy:** Deterministic Baseline  
**Cases evaluated:** 1000

- Recovered cases: **408** / 1000
- Recovery rate: **40.8000%**
- Total recovered amount: **₹462,997.6800**
- Total action cost: **₹980.5000**
- Total net value: **₹462,017.1800**
- Avg net value / case: **₹462.0172**
- Stop count: **71** (7.1000% of cases)
- Avg predicted ERV: N/A (rule-based policy; no ML predictions)

### Baseline Per-Action Recovery Breakdown

| Action | Count | Recovered | Recovery Rate | Total Net Value |
|--------|-------|-----------|---------------|-----------------|
| retry_now | 459 | 151 | 32.8976% | ₹139,437.2500 |
| retry_later | 221 | 130 | 58.8235% | ₹163,421.9000 |
| send_reminder | 117 | 26 | 22.2222% | ₹38,377.7700 |
| payment_method_update | 88 | 72 | 81.8182% | ₹93,007.6800 |
| recovery_link | 44 | 29 | 65.9091% | ₹27,772.5800 |
| stop | 71 | 0 | 0.0000% | ₹0.0000 |

---

## C. RecoverOS Policy Results

**Policy:** RecoverOS ML Policy (Expected Value + Guardrails)  
**Cases evaluated:** 1000

- Recovered cases: **705** / 1000
- Recovery rate: **70.5000%**
- Total recovered amount: **₹843,254.5800**
- Total action cost: **₹1,939.0000**
- Total net value: **₹841,315.5800**
- Avg net value / case: **₹841.3156**
- Stop count: **0** (0.0000% of cases)
- Avg predicted ERV (chosen action): **₹815.0237**
- Guardrail activations (Guardrail 2 + 3 counts per record): **189** (across all records; a record may trigger >1 guardrail)

### RecoverOS Per-Action Recovery Breakdown

| Action | Count | Recovered | Recovery Rate | Total Net Value |
|--------|-------|-----------|---------------|-----------------|
| retry_later | 605 | 440 | 72.7273% | ₹529,098.0900 |
| payment_method_update | 172 | 107 | 62.2093% | ₹109,078.5800 |
| recovery_link | 200 | 143 | 71.5000% | ₹178,423.2200 |
| escalate_human | 23 | 15 | 65.2174% | ₹24,715.6900 |

---

## D. Absolute Differences (RecoverOS − Baseline)

| Metric | Absolute Difference |
|--------|---------------------|
| Recovered count | +297 |
| Recovery rate | +0.297000 |
| Total recovered amount | ₹380,256.9000 |
| Total action cost | ₹958.5000 |
| Total net value | ₹379,298.4000 |
| Avg net value / case | ₹379.2984 |
| Stop count | -71 |
| Stop rate | -0.071000 |

---

## E. Relative Uplift (RecoverOS vs Baseline)

Relative uplift = (RecoverOS − Baseline) / |Baseline| × 100.  
N/A is shown when the baseline value is zero (undefined denominator).

| Metric | Relative Uplift |
|--------|-----------------|
| Recovery rate | +72.7941% |
| Total recovered amount | +82.1293% |
| Total action cost | +97.7562% |
| Total net value | +82.0962% |
| Avg net value / case | +82.0962% |
| Stop rate | -100.0000% |

---

## F. Action Distribution

| Action | Baseline Count | Baseline % | RecoverOS Count | RecoverOS % | Δ Count | Δ ppt |
|--------|---------------|------------|-----------------|-------------|---------|-------|
| retry_now | 459 | 45.9000% | 0 | 0.0000% | -459 | -45.9000 |
| retry_later | 221 | 22.1000% | 605 | 60.5000% | +384 | +38.4000 |
| send_reminder | 117 | 11.7000% | 0 | 0.0000% | -117 | -11.7000 |
| payment_method_update | 88 | 8.8000% | 172 | 17.2000% | +84 | +8.4000 |
| recovery_link | 44 | 4.4000% | 200 | 20.0000% | +156 | +15.6000 |
| escalate_human | 0 | 0.0000% | 23 | 2.3000% | +23 | +2.3000 |
| stop | 71 | 7.1000% | 0 | 0.0000% | -71 | -7.1000 |

---

## G. Interpretation

**Primary verdict (based on actual simulation outcomes):**
> RecoverOS exceeds baseline on total net value (actual simulation).

### Key observations

1. **Primary metric:** Total net value is the definitive comparison criterion,
   because it integrates both the simulator's ground-truth recovery outcomes
   and action execution costs.

2. **Recovery rate vs. net value trade-off:** A higher recovery rate does not
   necessarily imply higher net value if it is achieved at disproportionately
   higher cost (e.g., choosing `escalate_human` at ₹30 per case).

3. **Predicted ERV ≠ actual outcome:** RecoverOS chooses actions based on
   ML-predicted ERV. The simulator then draws an independent Bernoulli trial
   from the ground-truth probability, which differs from the predicted
   probability. The comparison is therefore based on simulator outcomes, not
   predicted values.

4. **Guardrails:** Deterministic safety guardrails constrain the ML policy's
   action space. Their effect on the action distribution is visible in Section F.

---

## H. Limitations

1. **Synthetic data:** Both the features and the simulator's ground-truth
   probabilities are derived from a synthetic dataset. Results may not
   generalise to real payment recovery dynamics.

2. **Single-action evaluation:** This evaluation assigns one action per case.
   Real recovery workflows are sequential; a multi-step evaluation would be
   more representative.

3. **Frozen model:** The model was trained on the training split of the same
   synthetic data distribution. Generalisation to a different distribution
   has not been evaluated.

4. **Stochasticity:** Despite the deterministic seed formula `seed = 42 + i`,
   the Bernoulli outcomes introduce sampling variance. Running more trials
   would reduce variance in the comparison.

5. **Single test population:** The held-out test set is 1,000 records.
   Statistical significance of the uplift has not been formally tested.

6. **No online feedback loop:** The ML model is evaluated offline.
   Online exploration, bandit feedback, or policy gradient methods could
   alter conclusions.

---

*End of RecoverOS Phase 2C Evaluation Report.*