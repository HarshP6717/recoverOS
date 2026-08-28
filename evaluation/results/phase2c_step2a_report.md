# RecoverOS Phase 2C Step 2A — Statistical Validation Report

**Generated:** 2026-08-28T07:34:45Z  
**Experiment:** Three-policy comparison with bootstrap confidence intervals

> All values derived from actual execution. Heuristic rules derived
> from simulator's published tables BEFORE inspecting test.csv outcomes.

---

## 1. Heuristic Design — Rule Derivation

### What rules does the heuristic use?

| Rule | Condition | Action | Justification |
|------|-----------|--------|---------------|
| 1 — Contact fatigue | contact_count ≥ 6 | send_reminder | Logit fatigue penalty ≥ −0.48 at contact=6; cheapest non-stop action |
| 2a — Hard failure | failure_type ∈ {expired_card, hard_decline, invalid_payment_method} AND attempt < 4 | payment_method_update | Base p(retry) ≤ 0.02; p(pmu) ≥ 0.70 |
| 2b — Hard failure, high attempt | Same hard failure types AND attempt ≥ 4 | recovery_link | After multiple attempts, recovery_link (cost ₹1.50) preferred over escalation |
| 3 — High attempt (non-hard) | attempt_number ≥ 4 | fallback per failure_type | attempt penalty = −0.32×(attempt−1); primary action significantly degraded |
| 4 — Primary action | All other cases | lookup by failure_type | Read directly from BASE_ACTION_PROBABILITIES (highest ERV at base probs) |
| 5 — Escalation upgrade | repeated_failure AND amount ≥ ₹200 AND attempt=1 AND contact ≤ 2 | escalate_human | Base p=0.76; ERV = 0.76×amt − 30 > 0 for all amounts ≥ ₹40 |

### Why are these rules defensible?

All thresholds are read from the simulator's published tables:
- `BASE_ACTION_PROBABILITIES` (lines 65–147 of `recovery_simulator.py`)
- `ACTION_COSTS`: retry=₹1, send_reminder=₹0.50, payment_method_update=₹2, recovery_link=₹1.50, escalate_human=₹30
- Contextual modifier coefficients: attempt_penalty=−0.32, fatigue_penalty=−0.12

The heuristic was designed by reading these tables and computing ERV rankings.
**No test.csv outcome was inspected before finalising any rule.**

---

## 2. Three-Policy Results

| Metric | Baseline | Heuristic | RecoverOS |
|--------|----------|-----------|-----------|
| Recovery Rate | 40.8000% | 70.1000% | 70.5000% |
| Recovered Count | 408 | 701 | 705 |
| Total Recovered (₹) | 462,997.6800 | 841,774.2200 | 843,254.5800 |
| Total Action Cost (₹) | 980.5000 | 1,754.5000 | 1,939.0000 |
| Total Net Value (₹) | 462,017.1800 | 840,019.7200 | 841,315.5800 |
| Avg Net Value/Case (₹) | 462.0172 | 840.0197 | 841.3156 |
| Stop Rate | 7.10% | 0.00% | 0.00% |

---

## 3. Uplift Summary

| Comparison | Abs Net Value Δ (₹) | Relative Uplift | Abs Recovery Rate Δ |
|------------|---------------------|-----------------|----------------------|
| RecoverOS vs Baseline | +379,298.4000 | +82.0962% | +29.7000 pp |
| RecoverOS vs Heuristic | +1,295.8600 | +0.1543% | +0.4000 pp |
| Heuristic vs Baseline | +378,002.5400 | +81.8157% | +29.3000 pp |

---

## 4. Bootstrap Confidence Intervals

N_BOOTSTRAPS = 1000, BOOTSTRAP_SEED = 0  
Method: resample 1,000 case indices with replacement; use fixed per-case outcomes (no re-simulation).

### RecoverOS vs Existing Baseline

- Point estimate (Total Net Value diff): **₹379,298.4000**
- 95% Bootstrap CI: **[₹318,247.9125, ₹446,393.6260]**
- CI crosses zero: **False**
- Point estimate (Recovery Rate diff): **+29.7000 pp**
- 95% Bootstrap CI (RR): **[+26.8975 pp, +32.7000 pp]**
- Bootstrap mean diff: ₹379,415.2318 ± ₹34,077.6468 (std)

### RecoverOS vs Strong Heuristic

- Point estimate (Total Net Value diff): **₹1,295.8600**
- 95% Bootstrap CI: **[₹-2,252.9130, ₹4,964.2892]**
- CI crosses zero: **True**
- Point estimate (Recovery Rate diff): **+0.4000 pp**
- 95% Bootstrap CI (RR): **[-0.1000 pp, +0.9000 pp]**
- Bootstrap mean diff: ₹1,325.6644 ± ₹1,770.6564 (std)

---

## 5. Paired Per-Case Analysis

### RecoverOS vs Existing Baseline

| Statistic | Value |
|-----------|-------|
| Mean Δ net value | ₹379.298400 |
| Median Δ net value | ₹0.000000 |
| Std Δ net value | ₹1,122.515944 |
| RecoverOS wins | 318 |
| Baseline wins | 235 |
| Ties | 447 |

Percentile distribution of per-case deltas (RecoverOS − Baseline):
p5=-1.5000, p25=+0.0000, p50=+0.0000, p75=+240.5025, p95=+1541.2330

### RecoverOS vs Strong Heuristic

| Statistic | Value |
|-----------|-------|
| Mean Δ net value | ₹1.295860 |
| Median Δ net value | ₹0.000000 |
| Std Δ net value | ₹58.127421 |
| RecoverOS wins | 53 |
| Heuristic wins | 105 |
| Ties | 842 |

Percentile distribution of per-case deltas (RecoverOS − Heuristic):
p5=-1.0000, p25=+0.0000, p50=+0.0000, p75=+0.0000, p95=+0.5000

---

## 6. Action-Level Analysis

| Action | B Count | B % | B RR | B NV (₹) | H Count | H % | H RR | H NV (₹) | R Count | R % | R RR | R NV (₹) |
|--------|---------|-----|------|-----------|---------|-----|------|-----------|---------|-----|------|-----------|
| retry_now | 459 | 45.9% | 0.3290 | 139437.25 | 0 | 0.0% | 0.0000 | 0.00 | 0 | 0.0% | 0.0000 | 0.00 |
| retry_later | 221 | 22.1% | 0.5882 | 163421.90 | 613 | 61.3% | 0.7308 | 534189.05 | 605 | 60.5% | 0.7273 | 529098.09 |
| send_reminder | 117 | 11.7% | 0.2222 | 38377.77 | 64 | 6.4% | 0.1094 | 7809.55 | 0 | 0.0% | 0.0000 | 0.00 |
| payment_method_update | 88 | 8.8% | 0.8182 | 93007.68 | 167 | 16.7% | 0.7485 | 151763.19 | 172 | 17.2% | 0.6221 | 109078.58 |
| recovery_link | 44 | 4.4% | 0.6591 | 27772.58 | 137 | 13.7% | 0.7664 | 130282.58 | 200 | 20.0% | 0.7150 | 178423.22 |
| escalate_human | 0 | 0.0% | 0.0000 | 0.00 | 19 | 1.9% | 0.8421 | 15975.35 | 23 | 2.3% | 0.6522 | 24715.69 |
| stop | 71 | 7.1% | 0.0000 | 0.00 | 0 | 0.0% | 0.0000 | 0.00 | 0 | 0.0% | 0.0000 | 0.00 |

---

## 7. Generalization Warning

> ⚠️ **This evaluation demonstrates performance WITHIN the synthetic simulator distribution.**
>
> It does NOT establish:
> - Real Razorpay payment recovery improvement
> - Real customer recovery probability
> - Real-world ROI
> - Production ML generalisation under covariate shift
>
> Both training data and the ground-truth simulator probability function are
> synthetic. The ML model was trained on the same synthetic distribution
> it is tested on. These results are valid for comparing the three policies
> under controlled, reproducible, synthetic conditions only.

---

## 8. Summary Findings

- The strong heuristic achieves total net value of **₹840,019.72** vs baseline ₹462,017.18 (++81.82%).
- RecoverOS achieves **₹841,315.58** net value.
- RecoverOS vs Baseline: +₹379,298.40 (+82.0962%). 95% CI: [₹318,247.91, ₹446,393.63]. CI crosses zero: **False**.
- RecoverOS vs Heuristic: +₹1,295.86 (+0.1543%). 95% CI: [₹-2,252.91, ₹4,964.29]. CI crosses zero: **True**.

> **Honest conclusion:** RecoverOS achieves a higher point estimate than the heuristic, but the 95% bootstrap CI crosses zero. The improvement is not statistically conclusive at this sample size.

### What this experiment still cannot prove

- That results generalise to real Razorpay data.
- That the +82.1% vs DeterministicBaseline is due to ML and not simply due to better feature usage.
- Statistical significance in the frequentist sense (no p-value computed).
- That the heuristic would remain weaker at a different hyperparameter or threshold choice.

---

*End of Phase 2C Step 2A Report.*