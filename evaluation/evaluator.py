"""
RecoverOS Phase 2C — Policy Evaluator (Exploratory — not used by the production DecisionEngine).

Runs both DeterministicBaselinePolicy and MLExpectedValuePolicy over the
held-out test population.

KEY DESIGN DECISIONS:
- Both policies receive exactly the same 1,000 test records in the same order.
- Ground-truth outcome for record i uses seed = 42 + i for both policies,
  ensuring a fair, deterministic comparison.
- Policy decisions and simulator calls are kept fully separated — the policy
  selects an action, then simulate_action generates the independent outcome.
- No fabricated, estimated, or hard-coded values.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.train import load_model_artifact
from simulator.policies import DeterministicBaselinePolicy, MLExpectedValuePolicy
from simulator.recovery_simulator import (
    ACTION_COSTS,
    ACTIONS,
    compute_expected_recovery_value,
    simulate_action,
)

# ── Constants ──────────────────────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_PATH = PROJECT_ROOT / "ml" / "models" / "recovery_model.joblib"
SEED_BASE = 42  # seed for record i = SEED_BASE + i
N_EXPECTED = 1000


# ── Internal helpers ───────────────────────────────────────────────────────────


def _load_test_data() -> pd.DataFrame:
    """Load the held-out test CSV and verify expected size."""
    path = DATA_DIR / "test.csv"
    if not path.exists():
        raise FileNotFoundError(f"Test dataset not found: {path}")
    df = pd.read_csv(path)
    if len(df) != N_EXPECTED:
        raise ValueError(
            f"Expected {N_EXPECTED} test records, found {len(df)} in {path}"
        )
    return df


def _load_ml_model() -> Any:
    """Load the frozen model artifact."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model artifact not found: {MODEL_PATH}")
    return load_model_artifact(MODEL_PATH)


def _compute_predicted_ervs(
    model: Any,
    test_df: pd.DataFrame,
) -> Dict[int, Dict[str, float]]:
    """
    Pre-compute predicted ERVs for every (record, action) pair in batch.

    Returns a mapping:  row_index -> {action: predicted_erv}

    This is only used for recording; the MLExpectedValuePolicy makes its own
    internal predictions independently (single-source-of-truth for selection).
    """
    n = len(test_df)
    n_actions = len(ACTIONS)

    expanded_dfs: List[pd.DataFrame] = []
    for action in ACTIONS:
        df_copy = test_df.copy()
        df_copy["action"] = action
        expanded_dfs.append(df_copy)

    full_df = pd.concat(expanded_dfs, ignore_index=True)
    all_probs = model.predict_proba(full_df)[:, 1]

    # Reshape: (n_actions, n) → transpose → (n, n_actions)
    prob_matrix = all_probs.reshape((n_actions, n)).T
    stop_idx = ACTIONS.index("stop")
    prob_matrix[:, stop_idx] = 0.0

    amounts = test_df["amount"].values
    cost_array = [ACTION_COSTS[a] for a in ACTIONS]

    per_record_ervs: Dict[int, Dict[str, float]] = {}
    for i in range(n):
        amt = float(amounts[i])
        per_record_ervs[i] = {}
        for j, action in enumerate(ACTIONS):
            p = float(prob_matrix[i, j])
            erv = compute_expected_recovery_value(amt, p, action)
            per_record_ervs[i][action] = erv

    return per_record_ervs


def _count_guardrails(record: Dict[str, Any], action: str) -> int:
    """
    Count how many guardrails were relevant to this (record, selected action).

    Guardrail 1: Negative ROI — action is 'stop' and all ERVs were <= 0.
    Guardrail 2: Retry suppression on hard failures.
    Guardrail 3: Human escalation suppression on micro-invoices.

    We record a count of 1 if any guardrail prevented an action (conservative
    counting: we flag the record, not the number of suppressed options).
    """
    failure_type = str(record.get("failure_type", "unknown"))
    amount = float(record.get("amount", 0.0))

    hard_failures = {"hard_decline", "expired_card", "invalid_payment_method"}

    triggered = 0
    if failure_type in hard_failures:
        triggered += 1  # Guardrail 2 active for this record
    if amount < 100.0:
        triggered += 1  # Guardrail 3 active for this record
    return triggered


# ── Main evaluation functions ──────────────────────────────────────────────────


def run_baseline_evaluation(
    test_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Evaluate DeterministicBaselinePolicy on the full test population.

    Parameters
    ----------
    test_df : pd.DataFrame
        The 1,000-record held-out test set.

    Returns
    -------
    List[Dict[str, Any]]
        One dict per case with all simulation outputs.
    """
    policy = DeterministicBaselinePolicy()
    chosen_actions: List[str] = policy.select_actions_batch(test_df)

    records: List[Dict[str, Any]] = []
    for i in range(len(test_df)):
        row = test_df.iloc[i]
        action = chosen_actions[i]
        seed = SEED_BASE + i

        sim = simulate_action(row, action, seed=seed)

        records.append(
            {
                "index": i,
                "action": sim["action"],
                "recovered": sim["recovered"],
                "recovered_amount": sim["recovered_amount"],
                "action_cost": sim["action_cost"],
                "net_value": sim["net_value"],
                "recovery_probability_gt": sim["recovery_probability"],
                "predicted_erv": None,       # Not applicable to baseline
                "guardrails_triggered": None, # Not applicable to baseline
            }
        )

    return records


def run_recoveros_evaluation(
    test_df: pd.DataFrame,
    model: Any,
) -> List[Dict[str, Any]]:
    """
    Evaluate MLExpectedValuePolicy (RecoverOS) on the full test population.

    Parameters
    ----------
    test_df : pd.DataFrame
        The 1,000-record held-out test set (same object as baseline).
    model : Any
        The frozen trained model artifact.

    Returns
    -------
    List[Dict[str, Any]]
        One dict per case with all simulation outputs, plus predicted ERV
        and guardrail counts.
    """
    policy = MLExpectedValuePolicy(model, guardrails_enabled=True)
    chosen_actions: List[str] = policy.select_actions_batch(test_df)

    # Pre-compute predicted ERVs for recording purposes
    predicted_ervs_by_index = _compute_predicted_ervs(model, test_df)

    records: List[Dict[str, Any]] = []
    for i in range(len(test_df)):
        row = test_df.iloc[i]
        action = chosen_actions[i]
        seed = SEED_BASE + i

        sim = simulate_action(row, action, seed=seed)

        # Predicted ERV for the chosen action
        pred_erv_chosen = predicted_ervs_by_index[i].get(action)

        # Count guardrails active on this record
        guardrails_count = _count_guardrails(dict(row), action)

        records.append(
            {
                "index": i,
                "action": sim["action"],
                "recovered": sim["recovered"],
                "recovered_amount": sim["recovered_amount"],
                "action_cost": sim["action_cost"],
                "net_value": sim["net_value"],
                "recovery_probability_gt": sim["recovery_probability"],
                "predicted_erv": pred_erv_chosen,
                "guardrails_triggered": guardrails_count,
            }
        )

    return records


def load_and_evaluate() -> Dict[str, Any]:
    """
    Entry point: loads data + model, runs both evaluations, returns all results.

    Returns
    -------
    Dict[str, Any]
        {
            "test_df": pd.DataFrame,
            "baseline_records": List[Dict],
            "recoveros_records": List[Dict],
        }
    """
    test_df = _load_test_data()
    model = _load_ml_model()

    baseline_records = run_baseline_evaluation(test_df)
    recoveros_records = run_recoveros_evaluation(test_df, model)

    # Sanity: both must cover the exact same test population in the same order
    assert len(baseline_records) == len(recoveros_records) == len(test_df), (
        "Record count mismatch — evaluation is not covering the same population."
    )
    for i, (b, r) in enumerate(zip(baseline_records, recoveros_records)):
        assert b["index"] == r["index"] == i, (
            f"Row index mismatch at position {i}: baseline={b['index']}, "
            f"recoveros={r['index']}"
        )

    return {
        "test_df": test_df,
        "baseline_records": baseline_records,
        "recoveros_records": recoveros_records,
    }
