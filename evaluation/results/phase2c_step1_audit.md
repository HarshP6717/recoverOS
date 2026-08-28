# RecoverOS Phase 2C Step 1 — Methodology Audit Report

**Generated:** 2026-08-28T07:15:46Z  
**Methodology Verdict:** `VALID WITH LIMITATIONS`

> All values derived from actual execution. No fabrication.

---

## Audit 1 — Identical Population

- Records in baseline evaluation: **1000**
- Records in RecoverOS evaluation: **1000**
- Records in test DataFrame: **1000**
- All counts equal: **True**
- Index mismatches: **0**
- First transaction ID: `tx_06490`
- Last transaction ID: `tx_07270`
- Independent reload identical: **True**

**Note:** RecoverOS uses all features; baseline uses only attempt_number+failure_type. Both receive the identical DataFrame row — no filtering difference exists. This asymmetry is by design and not a methodology flaw.

**Verdict:** PASS — both policies operate on identical records in identical order.

---

## Audit 2 — Seed Validity

- Seed base: **42**
- Formula: `seed = 42 + index`
- Source: evaluator loop variable i (0..999), not policy output
- Seed influenced by policy output: **False**
- Seed influenced by action selected: **False**
- Same seed → same outcome verified: **True**
- Cross-policy seeds equal: **True**
- All sample reproducibility checks passed: **True**

**Verdict:** PASS — seed = 42 + index is applied identically for both policies. Seed is determined by row position only, not by action or policy output. np.random.default_rng(seed) is instantiated fresh per call inside simulate_action.

---

## Audit 3 — Ground-Truth Independence

- GT function used: `compute_ground_truth_recovery_probability(record, action)`
- ML model inside `simulate_action()`: **False**
- ML prediction used as GT: **False**
- Policy influences simulator only via action: **True**
- All GT consistency checks passed: **True**

> Verified by code inspection: simulate_action() calls compute_ground_truth_recovery_probability() directly. The ML model's predict_proba() output is used ONLY inside MLExpectedValuePolicy.select_actions_batch() to choose an action. After action selection, the evaluator calls simulate_action(row, action, seed=seed) which internally calls compute_ground_truth_recovery_probability() — no ML model reference exists inside simulate_action().

**Verdict:** PASS — Ground-truth probability is computed entirely by compute_ground_truth_recovery_probability() inside simulate_action(). The ML model cannot influence the GT probability; it only determines which action is passed to the simulator.

---

## Audit 4 — Accounting Consistency

- Records checked: **1000**
- Net value formula: `net_value = recovered_amount - action_cost`
- Action cost source: ACTION_COSTS dict from simulator.recovery_simulator (shared)
- Recovered amount rule: `amount if recovered else 0.0`
- STOP behavior verified (zero cost, zero net value): **True**
- Accounting violations: **0**

**Verdict:** PASS

---

## Audit 5 — RecoverOS Decision Audit + 295 Unresolved Cases

- Total cases: **1000**
- Recovered: **705**
- Unresolved: **295**

- Avg GT recovery probability (recovered cases): **0.7327**
- Avg GT recovery probability (unresolved cases): **0.5893**
- Avg predicted ERV (unresolved): **₹656.5541**
- Total net value (unresolved, negative = cost paid but not recovered): **₹-620.5000**

### Unresolved by Failure Type

| Failure Type | Unresolved Count |
|---|---|
| insufficient_funds | 84 |
| bank_timeout | 43 |
| soft_decline | 38 |
| hard_decline | 36 |
| customer_abandoned | 28 |
| invalid_payment_method | 24 |
| unknown | 21 |
| repeated_failure | 11 |
| expired_card | 10 |

### Unresolved by Payment Method

| Payment Method | Unresolved Count |
|---|---|
| upi | 142 |
| card | 78 |
| netbanking | 35 |
| mandate_nach | 26 |
| wallet | 14 |

### Unresolved by Attempt Number

| Attempt # | Unresolved Count |
|---|---|
| 1 | 109 |
| 2 | 73 |
| 3 | 56 |
| 4 | 32 |
| 5 | 25 |

### Unresolved by Amount Bucket

| Amount Bucket (INR) | Unresolved Count |
|---|---|
| 100-500 | 63 |
| 1000-5000 | 122 |
| 500-1000 | 96 |
| 5000+ | 14 |

### Unresolved by Selected Action

| Action | Unresolved Count |
|---|---|
| retry_later | 165 |
| payment_method_update | 65 |
| recovery_link | 57 |
| escalate_human | 8 |

> **Explanation:** The 295 unresolved cases are not policy failures — they are stochastic losses. The ML policy selected the highest-predicted-ERV action under the guardrail constraints, but the ground-truth Bernoulli draw came out False (random() >= gt_prob). Average GT probability for unresolved cases is shown above; the expectation is that a fraction (1 - avg_gt_prob) of cases will not recover regardless of how good the policy is.

---

## Audit 6 — Action Quality

| Action | B Count | B Recovered | B Recovery Rate | B Net Value | R Count | R Recovered | R Recovery Rate | R Net Value |
|--------|---------|-------------|-----------------|-------------|---------|-------------|-----------------|-------------|
| retry_now | 459 | 151 | 0.3290 | ₹139437.25 | 0 | 0 | 0.0000 | ₹0.00 |
| retry_later | 221 | 130 | 0.5882 | ₹163421.90 | 605 | 440 | 0.7273 | ₹529098.09 |
| send_reminder | 117 | 26 | 0.2222 | ₹38377.77 | 0 | 0 | 0.0000 | ₹0.00 |
| payment_method_update | 88 | 72 | 0.8182 | ₹93007.68 | 172 | 107 | 0.6221 | ₹109078.58 |
| recovery_link | 44 | 29 | 0.6591 | ₹27772.58 | 200 | 143 | 0.7150 | ₹178423.22 |
| escalate_human | 0 | 0 | 0.0000 | ₹0.00 | 23 | 15 | 0.6522 | ₹24715.69 |
| stop | 71 | 0 | 0.0000 | ₹0.00 | 0 | 0 | 0.0000 | ₹0.00 |

### Net Value Delta by Action

| Action | Net Value Δ (₹) | % of Total Uplift |
|--------|-----------------|-------------------|
| retry_now | -139437.25 | -36.76% |
| retry_later | +365676.19 | +96.41% |
| send_reminder | -38377.77 | -10.12% |
| payment_method_update | +16070.90 | +4.24% |
| recovery_link | +150650.64 | +39.72% |
| escalate_human | +24715.69 | +6.52% |
| stop | +0.00 | +0.00% |

> **Primary driver:** The +82.1% net value uplift is primarily explained by action distribution shift. Baseline uses retry_now for 45.9% of cases — an action with 32.9% GT recovery rate on the test population's mix of failure types. RecoverOS shifts 60.5% of cases to retry_later, which achieves 72.7% GT recovery rate. Additionally, the baseline stops 71 cases (7.1%) that could still recover; RecoverOS acts on all 1,000. The net value delta from retry_later vs retry_now/send_reminder/stop alone explains the majority of the uplift.

---

## Audit 7 — Counterfactual Analysis

> ⚠️ **THIS IS A COUNTERFACTUAL ANALYSIS ONLY. The oracle uses unobservable ground-truth probabilities. It is NOT achievable in production. Results are shown for methodology assessment only.**

- Cases where RecoverOS selected the GT-optimal action: **902** (90.20%)
- Cases where RecoverOS selected a suboptimal action: **98**
- Actual total net value: **₹841,315.5800**
- Oracle total net value (same seeds): **₹843,918.7900**
- Missed net value opportunity vs oracle: **₹2,603.2100**

> When RecoverOS selects a suboptimal action vs the oracle, it is because the ML model's predicted probability does not perfectly match the GT probability. Reducing this gap requires better-calibrated ML predictions. Note: even with the oracle action, the Bernoulli outcome is stochastic, so oracle net value != guaranteed recovery.

---

## Audit 8 — Sequential Evaluation Feasibility

**Q1 — Can a failed first action lead to a second?**  
YES — technically. After simulate_action() returns recovered=False, a caller can choose a second action and call simulate_action() again. The simulator has no state; it is stateless and pure.

**Q2 — Can record state legitimately change between attempts?**  
NOT AUTOMATICALLY. compute_ground_truth_recovery_probability() reads attempt_number, contact_count, and days_overdue from the record. These would need to be MANUALLY incremented between attempts. The simulator provides no update mechanism — a caller must construct a new record dict with updated values.

**Q3 — How are attempt_number/contact_count/days_overdue handled?**  
attempt_number: read as-is, with penalty -0.32 * (attempt_num - 1). contact_count: read as-is, with fatigue penalty -0.12 * max(0, contact_count - 2). days_overdue: read as-is, with penalty -0.035 * min(days_overdue, 45). None of these are updated by the simulator. If sequential evaluation is to be principled, the caller must increment attempt_number by 1 and contact_count by 1 (for non-stop actions) between rounds.

**Q4 — Does the simulator provide state-transition rules?**  
NO. The simulator contains no state-transition function. There is no update_record(), no next_state(), and no documented rules for how attempt_number or days_overdue should evolve. Any state update rules would be invented assumptions, not simulator design.

**Q5 — Can a second action use a new deterministic seed?**  
YES — a deterministic seed can always be assigned. A natural choice is seed = 42 + index + round * 1000 or similar. However, the seed formula must be documented and applied identically to both policies.

**Q6 — Can the GT mechanism remain valid?**  
PARTIALLY. compute_ground_truth_recovery_probability() remains valid as long as the record features are correctly updated between rounds. If attempt_number is incremented, the attempt_penalty will apply correctly. If contact_count is not incremented, the fatigue_penalty will undercount fatigue. The function itself does not break — but its inputs must be meaningful.

**Q7 — Introduced assumptions for sequential evaluation:**

- Assumption A: attempt_number increases by 1 per round.
- Assumption B: contact_count increases by 1 for each non-stop action.
- Assumption C: days_overdue is constant within a sequential trial (no passage of real time is simulated).
- Assumption D: The recovery_probability for round 2 is computed on the updated record — but the BASE_ACTION_PROBABILITIES remain fixed (no fatigue beyond contact_count penalty).
- Assumption E: If the policy chooses 'stop' in round 1, the sequential trial ends. This must be enforced consistently.
- Assumption F: Seed assignment for round 2 must be deterministic and documented before any results are produced.
- Assumption G: The ML model was trained on single-action records — its predictions for attempt_number=2,3 may degrade in accuracy as the distribution shifts.

> **Conclusion:** The simulator supports sequential evaluation MECHANICALLY (it is stateless and pure) but NOT RIGOROUSLY without inventing state-transition rules. The minimum viable sequential protocol requires: (1) Increment attempt_number by 1 per round. (2) Increment contact_count by 1 per non-stop action. (3) Define and fix the seed formula for each round before execution. Without these, a sequential evaluation would produce results that are not reproducible, not comparable across policies, and not scientifically defensible. If these three rules are adopted as stated assumptions, sequential evaluation is feasible.

**Verdict:** FEASIBLE WITH EXPLICIT STATED ASSUMPTIONS. The simulator is stateless and supports re-calling. Sequential evaluation requires manually defined state-transition rules and a documented seed formula per round.

---

## Audit 9 — Scientific Validity Assessment

### What We Can Legitimately Claim

- On this specific 1,000-record synthetic test set, with seed=42+i, MLExpectedValuePolicy produces +82.1% higher total net value than DeterministicBaselinePolicy, as measured by the ground-truth simulator.
- The uplift is reproducible: running the evaluation again produces the same numbers.
- The primary mechanism of improvement is action selection quality: RecoverOS avoids retry_now (32.9% recovery rate) and prefers retry_later (72.7% rate).
- RecoverOS never violates its own safety guardrails (verified by 30 automated tests).
- The evaluation uses a clean separation: policy selects action; simulator generates independent ground-truth outcome.

### What We Cannot Claim

- That RecoverOS would outperform the baseline on real Razorpay payment data — the test set is synthetic and the simulator's GT probabilities are synthetic assumptions.
- That the +82.1% uplift is statistically significant — no confidence interval or p-value has been computed. 1,000 Bernoulli trials with no bootstrapping is thin.
- That the result generalises to sequential (multi-attempt) recovery scenarios — this is a single-action per case evaluation.
- That the model is well-calibrated on production data — it was trained and evaluated on the same synthetic distribution.
- That the guardrails are correctly specified for real business constraints — they are synthetic design choices.
- That the +82.1% is not partly explained by the baseline being deliberately weak (it ignores CLV, contact fatigue, and probabilistic information).

### Strongest Defensible Result

> On a 1,000-record synthetic held-out test set, with a deterministic ground-truth simulator and seed = 42 + i, MLExpectedValuePolicy (RecoverOS) achieves a total net value of ₹841,315.58 versus ₹462,017.18 for DeterministicBaselinePolicy, a measured difference of ₹379,298.40 (+82.1%). The improvement is driven primarily by the shift from retry_now (baseline-dominant, 32.9% recovery rate) to retry_later (RecoverOS-dominant, 72.7% recovery rate), which correctly reflects the simulator's higher base recovery probability for that action across the test population's failure-type mix.

### What a Technically Strong Interviewer Would Challenge

- Challenge 1 — Circularity: The ML model was trained on data generated by the same synthetic process that defines the test set. The baseline is unrealistically weak because it ignores all the rich features the simulator uses. This is an in-distribution evaluation against a strawman baseline.
- Challenge 2 — Sample size: 1,000 Bernoulli trials. Bootstrap the net value difference 1,000 times to produce a 95% CI. Without it, the +82.1% could be wide.
- Challenge 3 — Causality: The GT probabilities depend on attempt_number, contact_count, and days_overdue. The test set's distribution of these features is synthetic. A real population might have different distributions.
- Challenge 4 — Baseline choice: The deterministic baseline is a very low bar. A stronger baseline would be a heuristic that uses the same features as the simulator (e.g., pick the action with highest base probability for the given failure_type).
- Challenge 5 — Leakage suspicion: The ML model was trained on action+outcome pairs from the same synthetic generator. The GT probability used in training labels is the same formula used at test time. This is perfect in-distribution evaluation — real-world would have covariate shift.

### Recommended Next Experiment

> Strongest next evidence: Bootstrap the comparison 1,000 times (resample test cases with replacement, re-run both policies, record net value difference). Report 95% CI and p-value. Additionally, compare against a feature-aware heuristic baseline that uses failure_type → best-known action (without ML) to demonstrate that ML adds value beyond simple lookup. Only then does the +82.1% become defensible as an ML contribution.

---

*End of Phase 2C Step 1 Audit Report.*