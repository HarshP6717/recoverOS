import os
import sys
from pathlib import Path
import hashlib

# Add backend root to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

import json
import random
import statistics
from datetime import datetime, timezone
from typing import Dict, List, Any

from backend.app.schemas.recovery import DiagnosisRequest
from backend.app.services.decision_engine import DecisionEngine
import backend.app.services.decision_engine as de
import backend.app.core.config as config
from simulator.recovery_simulator import compute_ground_truth_recovery_probability
import simulator.recovery_simulator as sim

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')


class BatchEvaluator:
    def __init__(
        self,
        num_scenarios: int = 1000,
        seed: int = 42,
        output_path: Path | str | None = None,
    ):
        self.num_scenarios = num_scenarios
        self.seed = seed
        self.output_path = Path(output_path) if output_path else None
        self.decision_engine = DecisionEngine()
        
    def _get_isolated_seed(self, scenario_id: str, action: str) -> int:
        s = f"{self.seed}_{scenario_id}_{action}"
        return int(hashlib.md5(s.encode()).hexdigest(), 16) % (2**32 - 1)

    def _simulate_ground_truth(self, scenario: DiagnosisRequest, action: str) -> bool:
        record = {
            "amount": scenario.amount,
            "failure_type": scenario.failure_type,
            "payment_method": scenario.payment_method,
            "previous_failure_count": scenario.previous_failure_count,
            "attempt_number": scenario.attempt_number,
            "days_overdue": scenario.days_overdue,
            "customer_lifetime_value": scenario.customer_lifetime_value,
        }
        gt_prob = compute_ground_truth_recovery_probability(record, action)
        seed = self._get_isolated_seed(scenario.transaction_id, action)
        rng = random.Random(seed)
        return rng.random() < gt_prob
        
    def generate_scenarios(self) -> List[DiagnosisRequest]:
        random.seed(self.seed)
        scenarios = []
        failure_types = ["insufficient_funds", "bank_timeout", "fraud_suspected", "hard_decline"]
        
        for i in range(self.num_scenarios):
            amt = round(random.uniform(50.0, 5000.0), 2)
            ft = random.choices(failure_types, weights=[0.5, 0.3, 0.05, 0.15])[0]
            
            scenarios.append(DiagnosisRequest(
                transaction_id=f"tx_eval_{i}",
                customer_id=f"cust_{i}",
                subscription_id=f"sub_{i}",
                amount=amt,
                payment_method="card",
                failure_type=ft,
                attempt_number=random.randint(1, 4),
                days_overdue=random.randint(0, 10),
                customer_lifetime_value=round(random.uniform(100, 20000), 2),
                previous_failure_count=random.randint(0, 5)
            ))
        return scenarios
        
    def run_baseline(self, scenarios: List[DiagnosisRequest]) -> Dict[str, Any]:
        """
        Baseline Policy: Blindly retry up to 3 times, then stop.
        """
        recovered_amt = 0.0
        total_risk = sum(s.amount for s in scenarios)
        direct_cost = 0.0
        friction_cost = 0.0
        escalations = 0
        stops = 0
        
        for s in scenarios:
            if s.attempt_number <= 3:
                action = "retry_now"
                recovered = self._simulate_ground_truth(s, action)
                if recovered:
                    recovered_amt += s.amount
                    
                direct_cost += config.ACTION_COSTS.get(action, 0)
                friction_cost += config.ACTION_FRICTION_COSTS.get(action, 0)
            else:
                stops += 1
                
        return {
            "policy": "baseline_retry_3x",
            "total_risk": total_risk,
            "recovered_amount": recovered_amt,
            "recovery_rate": recovered_amt / total_risk if total_risk else 0,
            "direct_cost": direct_cost,
            "friction_cost": friction_cost,
            "net_recovered_value": recovered_amt - direct_cost - friction_cost,
            "escalations": escalations,
            "stops": stops
        }

    def run_recoveros(self, scenarios: List[DiagnosisRequest]) -> Dict[str, Any]:
        """
        RecoverOS Policy: ERV + Deterministic Guardrails
        """
        recovered_amt = 0.0
        total_risk = sum(s.amount for s in scenarios)
        direct_cost = 0.0
        friction_cost = 0.0
        escalations = 0
        stops = 0
        action_counts = {}
        ervs = []
        
        for s in scenarios:
            evals, selected_action, status, reason, guardrails, counterfactual = self.decision_engine.evaluate_request(s)
            
            action_counts[selected_action] = action_counts.get(selected_action, 0) + 1
            
            if selected_action == "escalate_human":
                escalations += 1
            elif selected_action == "stop":
                stops += 1
                
            direct_cost += config.ACTION_COSTS.get(selected_action, 0)
            friction_cost += config.ACTION_FRICTION_COSTS.get(selected_action, 0)
            
            chosen_ev = next((ev for ev in evals if ev.action == selected_action), None)
            if chosen_ev:
                ervs.append(chosen_ev.predicted_erv)
                
            recovered = self._simulate_ground_truth(s, selected_action)
            if recovered:
                recovered_amt += s.amount

        return {
            "policy": "recoveros_erv",
            "total_risk": total_risk,
            "recovered_amount": recovered_amt,
            "recovery_rate": recovered_amt / total_risk if total_risk else 0,
            "direct_cost": direct_cost,
            "friction_cost": friction_cost,
            "net_recovered_value": recovered_amt - direct_cost - friction_cost,
            "escalations": escalations,
            "stops": stops,
            "action_distribution": action_counts,
            "average_erv": statistics.mean(ervs) if ervs else 0
        }

    def run_evaluation_by_category(self, scenarios: List[DiagnosisRequest]) -> Dict[str, Any]:
        categories = set(s.failure_type for s in scenarios)
        breakdown = {}
        for cat in categories:
            cat_scenarios = [s for s in scenarios if s.failure_type == cat]
            baseline = self.run_baseline(cat_scenarios)
            recoveros = self.run_recoveros(cat_scenarios)
            breakdown[cat] = {
                "count": len(cat_scenarios),
                "baseline_net_value": baseline["net_recovered_value"],
                "recoveros_net_value": recoveros["net_recovered_value"],
                "delta": recoveros["net_recovered_value"] - baseline["net_recovered_value"]
            }
        return breakdown

    def _patch_costs(self, cost_mods=None, friction_mods=None):
        if cost_mods:
            config.ACTION_COSTS.update(cost_mods)
            de.ACTION_COSTS.update(cost_mods)
            sim.ACTION_COSTS.update(cost_mods)
        if friction_mods:
            config.ACTION_FRICTION_COSTS.update(friction_mods)
            de.ACTION_FRICTION_COSTS.update(friction_mods)

    def run_sensitivity_analysis(self, scenarios: List[DiagnosisRequest]) -> Dict[str, Any]:
        original_costs = config.ACTION_COSTS.copy()
        original_friction = config.ACTION_FRICTION_COSTS.copy()
        
        analyses = [
            ("baseline", None, None),
            ("lower_friction", None, {k: v * 0.5 for k, v in original_friction.items()}),
            ("higher_friction", None, {k: v * 2.0 for k, v in original_friction.items()}),
            ("lower_escalation_cost", {"escalate_human": original_costs.get("escalate_human", 30) * 0.5}, None),
            ("higher_escalation_cost", {"escalate_human": original_costs.get("escalate_human", 30) * 2.0}, None),
        ]
        
        results = {}
        for label, cost_mods, friction_mods in analyses:
            self._patch_costs(cost_mods, friction_mods)
            try:
                baseline = self.run_baseline(scenarios)
                recoveros = self.run_recoveros(scenarios)
                results[label] = {
                    "baseline_net_value": baseline["net_recovered_value"],
                    "recoveros_net_value": recoveros["net_recovered_value"],
                    "delta": recoveros["net_recovered_value"] - baseline["net_recovered_value"]
                }
            finally:
                self._patch_costs(original_costs, original_friction)
                
        return results

    def execute(self, output_path: Path | str | None = None) -> Dict[str, Any]:
        print("==================================================")
        print("RECOVEROS BENCHMARK (Deterministic Synthetic Evaluation)")
        print("==================================================")
        print("Dataset: 1,000 synthetically generated failed payments.")
        print("Seed: Fixed (42) for deterministic reproducible evaluation.")
        print("Scenario Distribution: 50% insufficient_funds, 30% bank_timeout, 15% hard_decline, 5% fraud_suspected")
        print("Baseline Policy: Blindly retry up to 3 times, then stop.")
        print("RecoverOS Policy: AI Diagnosis -> ERV Optimization -> Guardrails")
        print("Limitations: Simulation uses deterministic mock probabilities and synthetic costs.")
        print("==================================================\n")
        print(f"Generating {self.num_scenarios} synthetic scenarios (seed={self.seed})...")
        scenarios = self.generate_scenarios()
        
        print("Evaluating Baseline Policy...")
        baseline = self.run_baseline(scenarios)
        
        print("Evaluating RecoverOS Policy (ERV + AI Guardrails)...")
        recoveros = self.run_recoveros(scenarios)
        
        print("Running category breakdown...")
        category_breakdown = self.run_evaluation_by_category(scenarios)
        
        print("Running sensitivity analysis...")
        sensitivity = self.run_sensitivity_analysis(scenarios)
        
        results = {
            "benchmark_metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scenarios": self.num_scenarios,
                "seed": self.seed,
                "evaluation_type": "Deterministic synthetic evaluation",
                "erv_version": "v2_friction_adjusted"
            },
            "scenario_configuration": {
                "num_scenarios": self.num_scenarios,
                "failure_types_distribution": {"insufficient_funds": 0.5, "bank_timeout": 0.3, "fraud_suspected": 0.05, "hard_decline": 0.15}
            },
            "hidden_ground_truth_description": "Uses simulator.recovery_simulator.compute_ground_truth_recovery_probability to generate outcomes based on an isolated seed per scenario and action, ensuring policy predictions do not determine success.",
            "baseline_results": baseline,
            "recoveros_results": recoveros,
            "delta": {
                "net_value_delta": recoveros["net_recovered_value"] - baseline["net_recovered_value"],
                "friction_saved": baseline["friction_cost"] - recoveros["friction_cost"]
            },
            "per_category_results": category_breakdown,
            "sensitivity_analysis": sensitivity,
            "limitations": "Simulation uses deterministic mock probabilities and synthetic costs.",
            "reproducibility": "Uses deterministic seeds for scenario generation and simulated outcomes.",
            "disclaimer": "This is a deterministic synthetic evaluation based on documented simulation assumptions and is not evidence of production Razorpay recovery performance."
        }
        
        out_file = (
            Path(output_path)
            if output_path
            else (
                self.output_path
                if self.output_path
                else Path(__file__).resolve().parent.parent.parent / "evaluation" / "results" / "latest.json"
            )
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(out_file, "w") as f:
            json.dump(results, f, indent=2)
            
        print(f"Evaluation complete. Results saved to {out_file}")
        print("\n--- SUMMARY ---")
        print(f"Baseline Net Value:  ₹{baseline['net_recovered_value']:,.2f}")
        print(f"RecoverOS Net Value: ₹{recoveros['net_recovered_value']:,.2f}")
        print(f"Improvement:         +₹{results['delta']['net_value_delta']:,.2f}")
        
        print("\n--- PER-CATEGORY BREAKDOWN ---")
        for cat, data in category_breakdown.items():
            print(f"{cat.ljust(20)} | N={data['count']} | Baseline: ₹{data['baseline_net_value']:,.2f} | RecoverOS: ₹{data['recoveros_net_value']:,.2f} | Delta: +₹{data['delta']:,.2f}")

        return results

if __name__ == "__main__":
    evaluator = BatchEvaluator(num_scenarios=1000, seed=42)
    res = evaluator.execute()
    frozen_path = Path(__file__).resolve().parent.parent.parent / "evaluation" / "results" / "benchmark_1000_seed42.json"
    with open(frozen_path, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Frozen reference results saved to {frozen_path}")
