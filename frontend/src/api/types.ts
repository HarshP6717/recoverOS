export interface HealthResponse {
  status: string;
  database: string;
  diagnosis_engine_loaded: boolean;
  model_version: string;
  version: string;
}

export interface ActionCandidateEvaluation {
  action: string;
  predicted_recovery_probability: number;
  action_cost: number;
  predicted_erv: number;
  allowed: boolean;
  suppression_reason: string | null;
}

export interface CounterfactualData {
  selected_action: string;
  selected_erv: number;
  selected_probability: number;
  counterfactual_action: string;
  counterfactual_erv: number;
  counterfactual_probability: number;
  value_difference: number;
  guardrails_applied: string[];
}

export interface DashboardOverviewResponse {
  revenue_at_risk: number;
  recovered_amount: number;
  recovery_rate: number;
  recovery_cost: number;
  friction_cost: number | null;
  net_recovered_value: number;
  active_journeys: number;
  recovered_journeys: number;
  escalated_journeys: number;
  exhausted_journeys: number;
  cancellation_pending_count: number | null;
  execution_unknown_count: number;
}

export interface JourneySummary {
  journey_id: string;
  transaction_id: string;
  status: string;
  current_round: number;
  original_amount: number;
  recovered_amount: number;
  cumulative_cost: number;
  net_value: number;
  active_payment_link_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface JourneyListResponse {
  total: number;
  limit: number;
  offset: number;
  items: JourneySummary[];
}

export interface JourneyDetailResponse {
  journey_id: string;
  transaction_id: string;
  customer_id: string | null;
  subscription_id: string | null;
  amount: number;
  payment_method: string;
  failure_type: string;
  current_round: number;
  status: string;
  termination_reason: string | null;
  active_action: string | null;
  active_payment_link_id: string | null;
  active_payment_link_url: string | null;
  cumulative_cost: number;
  recovered_amount: number;
  net_value: number;
  contact_count: number;
  days_overdue: number;
  created_at: string | null;
  updated_at: string | null;
  
  latest_diagnosis_status: string | null;
  selected_action: string | null;
  counterfactual: CounterfactualData | null;
  guardrails_triggered: string[];
  candidate_evaluations: ActionCandidateEvaluation[];
  
  latest_execution_status: string | null;
  cancellation_pending: boolean | null;
  is_live_execution: boolean | null;
}

export interface TimelineEvent {
  timestamp: string;
  event_type: string;
  source: string;
  status: string;
  summary: string;
  financial_value: number | null;
  correlation_id: string | null;
  is_live: boolean;
}

export interface JourneyTimelineResponse {
  journey_id: string;
  events: TimelineEvent[];
}
