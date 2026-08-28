"""
RecoverOS Phase 2C Step 3 — Robustness Module: Slicing & Perturbation Engine.

All functions are read-only with respect to source data.
test.csv is loaded fresh each call and never mutated.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.recovery_simulator import ACTIONS, ACTION_COSTS, simulate_action
from evaluation.evaluator import SEED_BASE
from evaluation.policies.feature_aware_heuristic import StrongFeatureAwareHeuristic
from simulator.policies import DeterministicBaselinePolicy, MLExpectedValuePolicy

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Identity / key columns — must NEVER be perturbed
IDENTITY_COLS = frozenset([
    "transaction_id", "customer_id", "subscription_id",
])

# Categorical feature columns (can be set to NaN → "unknown")
CATEGORICAL_FEATURES = ["failure_type", "payment_method"]

# Numeric feature columns (can have Gaussian noise added)
NUMERIC_FEATURES = [
    "days_overdue", "previous_payment_count", "previous_success_count",
    "previous_failure_count", "previous_recovery_count",
    "customer_lifetime_value", "contact_count", "subscription_age_days",
    "previous_success_rate", "previous_recovery_rate",
]

# Pre-specified amount quantile thresholds (from scratch_data_analysis.py output)
AMOUNT_Q25 = 504.415   # 25th percentile on test set
AMOUNT_Q75 = 1500.528  # 75th percentile on test set

# Slices defined entirely from the DATA SCHEMA, not from test-outcome inspection
DISTRIBUTION_SLICES: Dict[str, Dict] = {
    "amount_low":          {"col": "amount",          "op": "lt",  "val": AMOUNT_Q25},
    "amount_high":         {"col": "amount",          "op": "gte", "val": AMOUNT_Q75},
    "attempt_early":       {"col": "attempt_number",  "op": "eq",  "val": 1},
    "attempt_late":        {"col": "attempt_number",  "op": "gte", "val": 3},
    "contact_fresh":       {"col": "contact_count",   "op": "lte", "val": 1},
    "contact_fatigued":    {"col": "contact_count",   "op": "gte", "val": 4},
    "payment_upi":         {"col": "payment_method",  "op": "eq",  "val": "upi"},
    "payment_card":        {"col": "payment_method",  "op": "eq",  "val": "card"},
    "payment_mandate_nach":{"col": "payment_method",  "op": "eq",  "val": "mandate_nach"},
    "failure_insufficient_funds":  {"col": "failure_type", "op": "eq", "val": "insufficient_funds"},
    "failure_bank_timeout":        {"col": "failure_type", "op": "eq", "val": "bank_timeout"},
    "failure_hard_failures":       {"col": "failure_type", "op": "in",
                                    "val": ["expired_card", "hard_decline", "invalid_payment_method"]},
    "failure_customer_abandoned":  {"col": "failure_type", "op": "eq", "val": "customer_abandoned"},
    "days_overdue_low":    {"col": "days_overdue",    "op": "lte", "val": 2},
    "days_overdue_high":   {"col": "days_overdue",    "op": "gte", "val": 10},
}

# Rare combinations identified from scratch_data_analysis.py
# n_test <= 5 in the test population
RARE_COMBOS = [
    ("customer_abandoned", "wallet"),
    ("hard_decline", "wallet"),
    ("invalid_payment_method", "mandate_nach"),
    ("invalid_payment_method", "wallet"),
    ("repeated_failure", "wallet"),
    ("soft_decline", "wallet"),
    ("unknown", "mandate_nach"),
    ("unknown", "netbanking"),
    ("unknown", "wallet"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Slice engine
# ─────────────────────────────────────────────────────────────────────────────

def apply_slice(df: pd.DataFrame, spec: Dict) -> pd.Series:
    """Return a boolean mask for the given slice spec."""
    col, op, val = spec["col"], spec["op"], spec["val"]
    s = df[col]
    if op == "eq":
        return s == val
    elif op == "lt":
        return s < val
    elif op == "lte":
        return s <= val
    elif op == "gt":
        return s > val
    elif op == "gte":
        return s >= val
    elif op == "in":
        return s.isin(val)
    else:
        raise ValueError(f"Unknown op: {op}")


# ─────────────────────────────────────────────────────────────────────────────
# Perturbation engine
# ─────────────────────────────────────────────────────────────────────────────

def perturb_categorical_missingness(
    df: pd.DataFrame,
    col: str,
    fraction: float,
    rng_seed: int,
) -> pd.DataFrame:
    """
    Set `fraction` of rows' values in `col` to 'unknown' (the simulator's
    fallback for unrecognised categories).

    Identity columns are never touched.
    The source DataFrame is NOT mutated; a copy is returned.
    """
    assert col not in IDENTITY_COLS, f"Cannot perturb identity col: {col}"
    df2 = df.copy()
    rng = np.random.default_rng(rng_seed)
    n = len(df2)
    indices = rng.choice(n, size=int(n * fraction), replace=False)
    df2.loc[df2.index[indices], col] = "unknown"
    return df2


def perturb_numeric_noise(
    df: pd.DataFrame,
    col: str,
    noise_std_fraction: float,   # noise std = noise_std_fraction * col.std()
    rng_seed: int,
    clip_min: Optional[float] = 0.0,
) -> pd.DataFrame:
    """
    Add Gaussian noise N(0, σ) where σ = noise_std_fraction * column_std().

    The source DataFrame is NOT mutated.
    Identity columns are never touched.
    Values are clipped to clip_min if provided.
    """
    assert col not in IDENTITY_COLS, f"Cannot perturb identity col: {col}"
    df2 = df.copy()
    col_std = float(df2[col].std())
    noise_std = noise_std_fraction * col_std
    rng = np.random.default_rng(rng_seed)
    noise = rng.normal(loc=0.0, scale=noise_std, size=len(df2))
    df2[col] = df2[col].values + noise
    if clip_min is not None:
        df2[col] = df2[col].clip(lower=clip_min)
    return df2


# ─────────────────────────────────────────────────────────────────────────────
# Per-record evaluation primitives
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_policy_on_df(
    df: pd.DataFrame,
    policy_fn,          # callable: df -> List[str]
    seed_offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Evaluate a policy on df.

    policy_fn(df) -> list of action strings.
    seed = (SEED_BASE + original_df_position + seed_offset).

    NOTE: The per-record seed uses the row's position in the PASSED df,
    not the original test.csv position.  This is intentional for slices —
    we want the randomness to follow the record's rank in the slice.
    Within a slice the seed is SEED_BASE + slice_position + seed_offset.
    """
    chosen_actions = policy_fn(df)
    records = []
    for i, (_, row) in enumerate(df.iterrows()):
        action = chosen_actions[i]
        seed = SEED_BASE + i + seed_offset
        sim = simulate_action(row, action, seed=seed)
        records.append({
            "slice_index": i,
            "action": sim["action"],
            "recovered": sim["recovered"],
            "recovered_amount": sim["recovered_amount"],
            "action_cost": sim["action_cost"],
            "net_value": sim["net_value"],
            "gt_prob": sim["recovery_probability"],
        })
    return records


def _metrics_from_records(records: List[Dict], label: str) -> Dict[str, Any]:
    """Aggregate per-record list into policy-level metrics dict."""
    if not records:
        return {
            "policy": label, "n": 0,
            "recovery_rate": 0.0, "total_net_value": 0.0,
            "avg_net_value": 0.0, "total_action_cost": 0.0,
            "action_distribution": {},
        }
    n = len(records)
    rec_count = sum(1 for r in records if r["recovered"])
    total_nv  = sum(r["net_value"] for r in records)
    total_cost = sum(r["action_cost"] for r in records)
    action_dist: Dict[str, int] = {}
    for r in records:
        action_dist[r["action"]] = action_dist.get(r["action"], 0) + 1
    return {
        "policy": label,
        "n": n,
        "recovery_rate": round(rec_count / n, 6),
        "recovered_count": rec_count,
        "total_net_value": round(total_nv, 4),
        "avg_net_value": round(total_nv / n, 4),
        "total_action_cost": round(total_cost, 4),
        "action_distribution": action_dist,
    }


def _safe_div(n: float, d: float) -> Optional[float]:
    return None if d == 0.0 else n / d


def _bootstrap_slice(
    records_a: List[Dict],
    records_b: List[Dict],
    n_bootstraps: int = 500,
    seed: int = 100,
) -> Dict[str, Any]:
    """Mini bootstrap for a single slice."""
    n = len(records_a)
    if n < 5:
        return {"n_bootstraps": 0, "ci_95": None,
                "point": round(sum(r["net_value"] for r in records_b) -
                               sum(r["net_value"] for r in records_a), 4),
                "crosses_zero": None, "note": "slice too small for bootstrap"}
    nv_a = np.array([r["net_value"] for r in records_a])
    nv_b = np.array([r["net_value"] for r in records_b])
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_bootstraps):
        idx = rng.integers(0, n, size=n)
        diffs.append(float(nv_b[idx].sum() - nv_a[idx].sum()))
    arr = np.array(diffs)
    lo, hi = float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))
    pt = float(nv_b.sum() - nv_a.sum())
    return {
        "n_bootstraps": n_bootstraps,
        "point": round(pt, 4),
        "ci_95": [round(lo, 4), round(hi, 4)],
        "crosses_zero": bool(lo < 0 < hi or hi < 0),
        "note": "",
    }


def _paired_wins(records_a, records_b, label_a, label_b) -> Dict[str, Any]:
    n = min(len(records_a), len(records_b))
    b_wins = sum(1 for i in range(n) if records_b[i]["net_value"] > records_a[i]["net_value"])
    a_wins = sum(1 for i in range(n) if records_a[i]["net_value"] > records_b[i]["net_value"])
    ties   = n - b_wins - a_wins
    diffs  = [records_b[i]["net_value"] - records_a[i]["net_value"] for i in range(n)]
    return {
        f"{label_b}_wins": b_wins,
        f"{label_a}_wins": a_wins,
        "ties": ties,
        "mean_delta": round(float(np.mean(diffs)), 6),
    }
