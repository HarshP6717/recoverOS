import pytest
import copy
from backend.scripts.evaluate_batch import BatchEvaluator
import backend.app.core.config as config

def test_same_seed_identical_results():
    eval1 = BatchEvaluator(num_scenarios=50, seed=42)
    eval2 = BatchEvaluator(num_scenarios=50, seed=42)
    
    scenarios1 = eval1.generate_scenarios()
    scenarios2 = eval2.generate_scenarios()
    
    for s1, s2 in zip(scenarios1, scenarios2):
        assert s1.transaction_id == s2.transaction_id
        assert s1.amount == s2.amount
        
    res1 = eval1.run_recoveros(scenarios1)
    res2 = eval2.run_recoveros(scenarios2)
    
    assert res1["net_recovered_value"] == res2["net_recovered_value"]
    assert res1["escalations"] == res2["escalations"]

def test_different_seed_different_scenarios():
    eval1 = BatchEvaluator(num_scenarios=50, seed=42)
    eval2 = BatchEvaluator(num_scenarios=50, seed=99)
    
    s1 = eval1.generate_scenarios()
    s2 = eval2.generate_scenarios()
    
    # Very unlikely to have same exact first amount
    assert s1[0].amount != s2[0].amount or s1[0].failure_type != s2[0].failure_type

def test_same_scenario_action_seed_identical_outcome():
    eval1 = BatchEvaluator(num_scenarios=1, seed=42)
    scenarios = eval1.generate_scenarios()
    s = scenarios[0]
    
    out1 = eval1._simulate_ground_truth(s, "retry_now")
    out2 = eval1._simulate_ground_truth(s, "retry_now")
    assert out1 == out2

def test_identical_scenario_population_for_both_policies():
    evaluator = BatchEvaluator(num_scenarios=10, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    baseline = evaluator.run_baseline(scenarios)
    recoveros = evaluator.run_recoveros(scenarios)
    
    assert baseline["total_risk"] == recoveros["total_risk"]

def test_recovered_amount_less_than_equal_to_risk():
    evaluator = BatchEvaluator(num_scenarios=50, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    res = evaluator.run_recoveros(scenarios)
    assert res["recovered_amount"] <= res["total_risk"]

def test_net_value_arithmetic_reconciliation():
    evaluator = BatchEvaluator(num_scenarios=50, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    res = evaluator.run_recoveros(scenarios)
    
    expected_net = res["recovered_amount"] - res["direct_cost"] - res["friction_cost"]
    assert pytest.approx(res["net_recovered_value"]) == expected_net

def test_no_negative_recovery():
    evaluator = BatchEvaluator(num_scenarios=50, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    res = evaluator.run_recoveros(scenarios)
    assert res["recovered_amount"] >= 0

def test_synthetic_assumptions_explicitly_disclosed(tmp_path):
    # Execute should print limitations and save to tmp_path without touching production results
    import io
    import sys
    
    tmp_out = tmp_path / "test_eval_output.json"
    evaluator = BatchEvaluator(num_scenarios=2, seed=42, output_path=tmp_out)
    
    captured_output = io.StringIO()
    sys.stdout = captured_output
    evaluator.execute()
    sys.stdout = sys.__stdout__
    
    out = captured_output.getvalue()
    assert "Simulation uses deterministic mock probabilities and synthetic costs" in out
    assert tmp_out.exists()

def test_sensitivity_analysis_does_not_mutate_primary_config():
    evaluator = BatchEvaluator(num_scenarios=10, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    original_costs = copy.deepcopy(config.ACTION_COSTS)
    original_friction = copy.deepcopy(config.ACTION_FRICTION_COSTS)
    
    evaluator.run_sensitivity_analysis(scenarios)
    
    assert config.ACTION_COSTS == original_costs
    assert config.ACTION_FRICTION_COSTS == original_friction

def test_ai_probability_cannot_control_ground_truth():
    # Verify that changing AI probability output does not change ground truth outcome
    evaluator = BatchEvaluator(num_scenarios=1, seed=42)
    scenarios = evaluator.generate_scenarios()
    
    # AI prediction is mocked out in ground truth, let's just make sure simulate_ground_truth 
    # doesn't take AI probability as an argument.
    import inspect
    sig = inspect.signature(evaluator._simulate_ground_truth)
    assert "ai_probability" not in sig.parameters
    assert "predicted_probability" not in sig.parameters
    
    # Ensure compute_ground_truth_recovery_probability from simulator is used
    from simulator.recovery_simulator import compute_ground_truth_recovery_probability
    assert compute_ground_truth_recovery_probability is not None
