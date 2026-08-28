"""
Audit script for Phase 1 Policy Results.
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Enable UTF-8 for console output on Windows platforms
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from ml.train import load_model_artifact
from simulator.recovery_simulator import (
    ACTIONS,
    ACTION_COSTS,
    compute_expected_recovery_value,
    compute_ground_truth_recovery_probability,
    evaluate_all_actions_ground_truth,
)
from simulator.policies import (
    DeterministicBaselinePolicy,
    MLExpectedValuePolicy,
    GroundTruthOraclePolicy,
)

DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
test_df = pd.read_csv(DATA_PROCESSED_DIR / "test.csv")
model = load_model_artifact()

# 1. Expand candidate actions for all test cases and get predictions
n_records = len(test_df)
expanded_dfs = []
for action in ACTIONS:
    action_df = test_df.copy()
    action_df["action"] = action
    expanded_dfs.append(action_df)

full_candidate_df = pd.concat(expanded_dfs, ignore_index=True)
all_pred_probs = model.predict_proba(full_candidate_df)[:, 1]

n_actions = len(ACTIONS)
prob_matrix = all_pred_probs.reshape((n_actions, n_records)).T

# Set 'stop' prob to 0.0
stop_idx = ACTIONS.index("stop")
prob_matrix[:, stop_idx] = 0.0

cost_array = np.array([ACTION_COSTS[a] for a in ACTIONS])
amounts = test_df["amount"].values
failure_types = test_df["failure_type"].values
attempt_numbers = test_df["attempt_number"].values

# Calculate predicted ERV matrix (n_records, n_actions)
pred_erv_matrix = (amounts[:, np.newaxis] * prob_matrix) - cost_array[np.newaxis, :]
# For stop, ERV is strictly 0.0
pred_erv_matrix[:, stop_idx] = 0.0

# Calculate ground-truth ERV matrix
gt_erv_matrix = np.zeros((n_records, n_actions))
gt_prob_matrix = np.zeros((n_records, n_actions))
for i in range(n_records):
    row = test_df.iloc[i]
    for j, a in enumerate(ACTIONS):
        p = compute_ground_truth_recovery_probability(row, a)
        gt_prob_matrix[i, j] = p
        gt_erv_matrix[i, j] = compute_expected_recovery_value(row["amount"], p, a)
gt_erv_matrix[:, stop_idx] = 0.0

# Guardrail suppression tracking
hard_failures = {"hard_decline", "expired_card", "invalid_payment_method"}
retry_now_idx = ACTIONS.index("retry_now")
retry_later_idx = ACTIONS.index("retry_later")
escalate_idx = ACTIONS.index("escalate_human")

guardrail_hard_failure_suppressed = 0
guardrail_micro_amount_suppressed = 0
guardrail_negative_erv_stop = 0

guarded_pred_erv_matrix = pred_erv_matrix.copy()

for i in range(n_records):
    amt = amounts[i]
    ftype = failure_types[i]

    if ftype in hard_failures:
        guardrail_hard_failure_suppressed += 1
        guarded_pred_erv_matrix[i, retry_now_idx] = -1e9
        guarded_pred_erv_matrix[i, retry_later_idx] = -1e9

    if amt < 100.0:
        guardrail_micro_amount_suppressed += 1
        guarded_pred_erv_matrix[i, escalate_idx] = -1e9

# Max predicted ERV across candidate actions per record (excluding stop)
non_stop_indices = [idx for idx, a in enumerate(ACTIONS) if a != "stop"]
max_non_stop_pred_erv = np.max(pred_erv_matrix[:, non_stop_indices], axis=1)
max_guarded_pred_erv = np.max(guarded_pred_erv_matrix, axis=1)

# Count cases where all candidate actions have ERV <= 0
cases_all_candidate_erv_le_0 = np.sum(max_non_stop_pred_erv <= 0.0)
cases_guarded_all_erv_le_0 = np.sum(max_guarded_pred_erv <= 0.0)

print(f"Total Test Cases: {n_records}")
print(f"Amount Range: Min = ₹{amounts.min():.2f}, Median = ₹{np.median(amounts):.2f}, Max = ₹{amounts.max():.2f}")
print(f"\n--- 1. PREDICTED ERV STATISTICS (Max across candidate actions per case) ---")
print(f"Min Max-Pred-ERV:    ₹{np.min(max_non_stop_pred_erv):.2f}")
print(f"25th percentile:     ₹{np.percentile(max_non_stop_pred_erv, 25):.2f}")
print(f"Median Max-Pred-ERV: ₹{np.median(max_non_stop_pred_erv):.2f}")
print(f"75th percentile:     ₹{np.percentile(max_non_stop_pred_erv, 75):.2f}")
print(f"Max Max-Pred-ERV:    ₹{np.max(max_non_stop_pred_erv):.2f}")

print(f"\n--- 2. CASES WITH ALL CANDIDATE ERVs <= 0 ---")
print(f"Raw un-guarded: {cases_all_candidate_erv_le_0} / {n_records}")
print(f"Guarded:        {cases_guarded_all_erv_le_0} / {n_records}")

print(f"\n--- 3. GUARDRAIL SUPPRESSIONS (out of {n_records} cases) ---")
print(f"Guardrail 2 (Hard Failure Retry Suppression): {guardrail_hard_failure_suppressed} cases")
print(f"Guardrail 3 (Micro-Amount < ₹100 Suppression): {guardrail_micro_amount_suppressed} cases")

# Why did baseline select stop 71 times?
base_policy = DeterministicBaselinePolicy()
base_actions = base_policy.select_actions_batch(test_df)
base_stop_count = base_actions.count("stop")
print(f"\n--- 4. BASELINE STOP ANALYSIS ---")
print(f"Baseline STOP count: {base_stop_count}")
# Which attempt numbers caused baseline stop?
baseline_stop_df = test_df[[act == 'stop' for act in base_actions]]
print(f"Attempt numbers for baseline STOP cases:")
print(baseline_stop_df['attempt_number'].value_counts().to_dict())

# What were the ML policy's chosen actions and predicted/ground-truth ERVs on those 71 cases?
ml_policy = MLExpectedValuePolicy(model, guardrails_enabled=True)
ml_actions = ml_policy.select_actions_batch(test_df)

baseline_stop_indices = [i for i, act in enumerate(base_actions) if act == 'stop']
ml_actions_on_base_stop = [ml_actions[i] for i in baseline_stop_indices]
print(f"\nML Actions chosen on the {len(baseline_stop_indices)} baseline STOP cases:")
print(pd.Series(ml_actions_on_base_stop).value_counts().to_dict())

# Let's inspect the ERVs and probabilities on these 71 cases
base_stop_amounts = amounts[baseline_stop_indices]
base_stop_max_ervs = max_guarded_pred_erv[baseline_stop_indices]
print(f"\nOn Baseline STOP cases (Attempt >= 4):")
print(f"  Min Amount: ₹{base_stop_amounts.min():.2f}, Median Amount: ₹{np.median(base_stop_amounts):.2f}")
print(f"  Min Max-Pred-ERV: ₹{base_stop_max_ervs.min():.2f}, Median Max-Pred-ERV: ₹{np.median(base_stop_max_ervs):.2f}")

# Look at the lowest ERVs across the entire test set
lowest_erv_indices = np.argsort(max_guarded_pred_erv)[:10]
print(f"\n--- Top 10 lowest max-ERV cases in entire test set ---")
for idx in lowest_erv_indices:
    row = test_df.iloc[idx]
    best_act_idx = np.argmax(guarded_pred_erv_matrix[idx])
    best_act = ACTIONS[best_act_idx]
    best_erv = guarded_pred_erv_matrix[idx, best_act_idx]
    best_prob = prob_matrix[idx, best_act_idx]
    cost = ACTION_COSTS[best_act]
    print(f"Idx {idx}: Amount=₹{row['amount']}, Failure={row['failure_type']}, Attempt={row['attempt_number']}, DaysOverdue={row['days_overdue']}, BestAction={best_act}, P_pred={best_prob:.4f}, Cost=₹{cost:.2f}, PredERV=₹{best_erv:.2f}")
