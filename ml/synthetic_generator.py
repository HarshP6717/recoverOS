"""
RecoverOS Synthetic Payment Failure Dataset Generator.

Generates a reproducible dataset of exactly 10,000 failed subscription payment records
with realistic Indian SaaS / subscription characteristics (INR ₹), realistic inter-feature
correlations, historical action assignments, and ground-truth simulated outcomes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.recovery_simulator import (
    ACTIONS,
    FAILURE_TYPES,
    PAYMENT_METHODS,
    compute_ground_truth_recovery_probability,
    simulate_action,
)
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def generate_synthetic_dataset(
    n_records: int = 10000,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generates exactly n_records synthetic subscription payment failure cases.

    Parameters
    ----------
    n_records : int
        Number of records to generate (default: 10,000).
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    pd.DataFrame
        Complete DataFrame with 10,000 records.
    """
    rng = np.random.default_rng(seed)

    # 1. Unique identifiers and entities
    n_customers = int(n_records * 0.40)  # ~4,000 unique customers
    n_subscriptions = int(n_records * 0.55)  # ~5,500 subscriptions

    customer_pool = [f"cust_{i:05d}" for i in range(n_customers)]
    subscription_pool = [f"sub_{i:05d}" for i in range(n_subscriptions)]

    customer_ids = rng.choice(customer_pool, size=n_records)
    subscription_ids = rng.choice(subscription_pool, size=n_records)
    transaction_ids = [f"tx_{i:05d}" for i in range(n_records)]

    # 2. Payment methods (realistic Indian payment ecosystem distribution)
    # UPI (45%), Card (30%), Netbanking (10%), Mandate NACH (10%), Wallet (5%)
    payment_method_probs = [0.45, 0.30, 0.10, 0.10, 0.05]
    payment_methods = rng.choice(PAYMENT_METHODS, size=n_records, p=payment_method_probs)

    # 3. Realistic subscription pricing tiers in INR (₹)
    tier_base_amounts = np.array([199.0, 499.0, 999.0, 1499.0, 2499.0, 4999.0, 9999.0])
    tier_probs = np.array([0.22, 0.32, 0.22, 0.12, 0.07, 0.04, 0.01])

    # Sample base tiers with slight noise / add-on amounts
    chosen_tiers = rng.choice(tier_base_amounts, size=n_records, p=tier_probs)
    addon_noise = rng.uniform(0.0, 50.0, size=n_records).round(2)
    amounts = np.round(chosen_tiers + addon_noise, 2)

    # 4. Attempt numbers (decaying distribution: most failures caught at attempt 1 or 2)
    attempt_probs = [0.55, 0.25, 0.12, 0.05, 0.03]
    attempt_numbers = rng.choice([1, 2, 3, 4, 5], size=n_records, p=attempt_probs)

    # 5. Days overdue correlated with attempt number
    days_overdue = np.zeros(n_records, dtype=int)
    for i in range(n_records):
        att = attempt_numbers[i]
        if att == 1:
            days_overdue[i] = rng.integers(0, 3)
        elif att == 2:
            days_overdue[i] = rng.integers(3, 7)
        elif att == 3:
            days_overdue[i] = rng.integers(7, 15)
        elif att == 4:
            days_overdue[i] = rng.integers(15, 25)
        else:
            days_overdue[i] = rng.integers(25, 45)

    # 6. Contact count correlated with days overdue and attempt number
    contact_counts = np.zeros(n_records, dtype=int)
    for i in range(n_records):
        att = attempt_numbers[i]
        days = days_overdue[i]
        base_contacts = att - 1
        extra_contacts = int(days // 6)
        noise = rng.choice([0, 1], p=[0.7, 0.3])
        contact_counts[i] = min(8, base_contacts + extra_contacts + noise)

    # 7. Customer payment history
    # Previous payment counts (tenure in billing cycles: 1 to 36 months)
    prev_payment_counts = rng.integers(1, 36, size=n_records)

    # Success rate strongly influenced by customer quality / tenure
    # Long-standing customers tend to have higher historical success rates (~85-95%)
    historical_quality = rng.beta(a=8, b=2, size=n_records)  # mean ~0.80
    prev_success_counts = np.round(prev_payment_counts * historical_quality).astype(int)
    # Ensure success count is strictly <= payment count
    prev_success_counts = np.minimum(prev_success_counts, prev_payment_counts)
    prev_failure_counts = prev_payment_counts - prev_success_counts

    # Previous recovery count is a subset of previous failures
    prev_recovery_counts = np.zeros(n_records, dtype=int)
    for i in range(n_records):
        fail_cnt = prev_failure_counts[i]
        if fail_cnt > 0:
            rec_rate = rng.beta(a=4, b=3)  # average recovery rate ~0.57
            prev_recovery_counts[i] = int(np.round(fail_cnt * rec_rate))
        else:
            prev_recovery_counts[i] = 0

    # 8. Subscription age in days
    subscription_age_days = (prev_payment_counts * 30) + days_overdue + rng.integers(1, 28, size=n_records)

    # 9. Customer Lifetime Value in INR (₹)
    # Strongly correlated with previous successful payments and tier amount
    clv_base = prev_success_counts * amounts
    clv_tenure_bonus = subscription_age_days * 1.5
    clv_noise = rng.uniform(0.9, 1.1, size=n_records)
    customer_ltv = np.round((clv_base + clv_tenure_bonus) * clv_noise, 2)

    # 10. Failure types with realistic conditional correlations to payment method & attempt number
    failure_types = []
    for i in range(n_records):
        pm = payment_methods[i]
        att = attempt_numbers[i]
        sub_age = subscription_age_days[i]

        if att >= 4:
            # High attempt number often degenerates into repeated failure or hard decline
            p_dist = {
                "insufficient_funds": 0.15,
                "bank_timeout": 0.05,
                "soft_decline": 0.10,
                "expired_card": 0.10 if pm == "card" else 0.00,
                "hard_decline": 0.25,
                "invalid_payment_method": 0.10,
                "customer_abandoned": 0.05,
                "repeated_failure": 0.15 if pm != "card" else 0.05,
                "unknown": 0.05,
            }
        elif pm == "card":
            # Cards suffer from expiry (especially on older subscriptions), soft declines, insufficient funds
            expiry_weight = 0.25 if sub_age > 365 else 0.08
            p_dist = {
                "insufficient_funds": 0.28,
                "bank_timeout": 0.08,
                "soft_decline": 0.22,
                "expired_card": expiry_weight,
                "hard_decline": 0.08,
                "invalid_payment_method": 0.06,
                "customer_abandoned": 0.10,
                "repeated_failure": 0.05,
                "unknown": 0.05,
            }
        elif pm == "upi":
            # UPI has customer abandonments (3DS / collect request drops), bank timeouts, insufficient funds
            p_dist = {
                "insufficient_funds": 0.30,
                "bank_timeout": 0.26,
                "soft_decline": 0.08,
                "expired_card": 0.00,
                "hard_decline": 0.04,
                "invalid_payment_method": 0.08,
                "customer_abandoned": 0.18,
                "repeated_failure": 0.03,
                "unknown": 0.03,
            }
        elif pm == "mandate_nach":
            # NACH mandates have insufficient funds (returns) and bank processing timeouts
            p_dist = {
                "insufficient_funds": 0.42,
                "bank_timeout": 0.22,
                "soft_decline": 0.12,
                "expired_card": 0.00,
                "hard_decline": 0.08,
                "invalid_payment_method": 0.06,
                "customer_abandoned": 0.02,
                "repeated_failure": 0.05,
                "unknown": 0.03,
            }
        elif pm == "netbanking":
            # Netbanking has session timeouts and customer dropouts
            p_dist = {
                "insufficient_funds": 0.25,
                "bank_timeout": 0.32,
                "soft_decline": 0.10,
                "expired_card": 0.00,
                "hard_decline": 0.05,
                "invalid_payment_method": 0.05,
                "customer_abandoned": 0.15,
                "repeated_failure": 0.04,
                "unknown": 0.04,
            }
        else:  # wallet
            p_dist = {
                "insufficient_funds": 0.45,
                "bank_timeout": 0.15,
                "soft_decline": 0.10,
                "expired_card": 0.00,
                "hard_decline": 0.05,
                "invalid_payment_method": 0.05,
                "customer_abandoned": 0.12,
                "repeated_failure": 0.04,
                "unknown": 0.04,
            }

        # Normalize probability vector to sum to 1.0
        keys = list(p_dist.keys())
        raw_p = np.array([p_dist[k] for k in keys])
        norm_p = raw_p / raw_p.sum()
        chosen_ft = rng.choice(keys, p=norm_p)
        failure_types.append(chosen_ft)

    # 11. Historical Action Assignment Policy
    # In historical production data, actions were selected by a historical policy mix:
    # 70% rule-based dunning, 20% manual/varied workflows, 10% random exploratory actions
    # This provides good coverage across all 7 actions for training.
    historical_actions = []
    for i in range(n_records):
        att = attempt_numbers[i]
        ft = failure_types[i]
        strategy_mode = rng.choice(["rule_dunning", "varied_workflow", "random_explore"], p=[0.60, 0.25, 0.15])

        if strategy_mode == "rule_dunning":
            if att == 1:
                historical_actions.append("payment_method_update" if ft in ["expired_card", "hard_decline"] else "retry_now")
            elif att == 2:
                historical_actions.append("recovery_link" if ft in ["expired_card", "customer_abandoned"] else "retry_later")
            elif att == 3:
                historical_actions.append("send_reminder")
            elif att == 4:
                historical_actions.append("escalate_human" if amounts[i] > 1000 else "send_reminder")
            else:
                historical_actions.append("stop")
        elif strategy_mode == "varied_workflow":
            if ft in ["expired_card", "invalid_payment_method", "hard_decline"]:
                historical_actions.append(rng.choice(["payment_method_update", "recovery_link", "escalate_human", "stop"]))
            elif ft == "bank_timeout":
                historical_actions.append(rng.choice(["retry_now", "retry_later", "send_reminder"]))
            elif ft == "insufficient_funds":
                historical_actions.append(rng.choice(["retry_later", "send_reminder", "recovery_link", "stop"]))
            elif ft == "customer_abandoned":
                historical_actions.append(rng.choice(["recovery_link", "send_reminder", "retry_later"]))
            else:
                historical_actions.append(rng.choice(ACTIONS))
        else:  # random_explore
            historical_actions.append(rng.choice(ACTIONS))

    # 12. Assemble base DataFrame
    df = pd.DataFrame({
        "transaction_id": transaction_ids,
        "customer_id": customer_ids,
        "subscription_id": subscription_ids,
        "amount": amounts,
        "payment_method": payment_methods,
        "failure_type": failure_types,
        "attempt_number": attempt_numbers,
        "days_overdue": days_overdue,
        "previous_payment_count": prev_payment_counts,
        "previous_success_count": prev_success_counts,
        "previous_failure_count": prev_failure_counts,
        "previous_recovery_count": prev_recovery_counts,
        "customer_lifetime_value": customer_ltv,
        "contact_count": contact_counts,
        "subscription_age_days": subscription_age_days,
        "action": historical_actions,
    })

    # 13. Derive rates
    df["previous_success_rate"] = np.round(
        df["previous_success_count"] / np.maximum(1, df["previous_payment_count"]), 4
    )
    df["previous_recovery_rate"] = np.round(
        df["previous_recovery_count"] / np.maximum(1, df["previous_failure_count"]), 4
    )

    # 14. Simulate ground-truth outcome for each record under its historical action
    rec_probs = []
    costs = []
    ervs = []
    recovered_flags = []
    recovered_amts = []
    net_values = []

    for idx, row in df.iterrows():
        # Use deterministic sub-seed per row index for exact reproducibility
        sim_res = simulate_action(row, row["action"], seed=seed + idx)
        rec_probs.append(round(sim_res["recovery_probability"], 4))
        costs.append(round(sim_res["action_cost"], 2))
        ervs.append(round(sim_res["expected_recovery_value"], 2))
        recovered_flags.append(sim_res["recovered"])
        recovered_amts.append(round(sim_res["recovered_amount"], 2))
        net_values.append(round(sim_res["net_value"], 2))

    df["recovery_probability"] = rec_probs
    df["action_cost"] = costs
    df["expected_recovery_value"] = ervs
    df["recovered"] = recovered_flags
    df["recovered_amount"] = recovered_amts
    df["net_value"] = net_values

    return df


def save_dataset_splits(
    df: pd.DataFrame,
    train_size: int = 8000,
    val_size: int = 1000,
    test_size: int = 1000,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Saves the complete raw dataset and partitioned train/val/test splits.

    Parameters
    ----------
    df : pd.DataFrame
        Complete DataFrame (10,000 records).
    train_size : int
        Number of training records (default: 8,000).
    val_size : int
        Number of validation records (default: 1,000).
    test_size : int
        Number of test records (default: 1,000).
    seed : int
        Random seed for shuffling before partition.

    Returns
    -------
    Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
        (train_df, val_df, test_df)
    """
    total_required = train_size + val_size + test_size
    if len(df) != total_required:
        raise ValueError(f"Expected {total_required} records, received {len(df)}")

    # Ensure directories exist
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Save raw complete dataset
    raw_path = DATA_RAW_DIR / "payment_failures_10k.csv"
    df.to_csv(raw_path, index=False)

    # Reproducible shuffle before splitting
    shuffled_df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    train_df = shuffled_df.iloc[:train_size].reset_index(drop=True)
    val_df = shuffled_df.iloc[train_size : train_size + val_size].reset_index(drop=True)
    test_df = shuffled_df.iloc[train_size + val_size :].reset_index(drop=True)

    # Save processed splits
    train_path = DATA_PROCESSED_DIR / "train.csv"
    val_path = DATA_PROCESSED_DIR / "val.csv"
    test_path = DATA_PROCESSED_DIR / "test.csv"

    train_df.to_csv(train_path, index=False)
    val_df.to_csv(val_path, index=False)
    test_df.to_csv(test_path, index=False)

    print(f"Dataset generated and saved successfully:")
    print(f"  Raw:   {raw_path} ({len(df)} records)")
    print(f"  Train: {train_path} ({len(train_df)} records)")
    print(f"  Val:   {val_path} ({len(val_df)} records)")
    print(f"  Test:  {test_path} ({len(test_df)} records)")

    return train_df, val_df, test_df


if __name__ == "__main__":
    print("Generating 10,000 synthetic payment failure records (seed=42)...")
    dataset = generate_synthetic_dataset(n_records=10000, seed=42)
    save_dataset_splits(dataset, train_size=8000, val_size=1000, test_size=1000, seed=42)
