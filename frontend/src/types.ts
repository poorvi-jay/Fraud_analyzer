export type Verdict = "allow" | "escalate" | "block";

export interface TransactionListItem {
  id: string;
  user_id: string;
  amount: number;
  transaction_type: string;
  location_country: string;
  occurred_at: string;
  final_verdict: Verdict | null;
}

export interface AgentOpinion {
  agent_name: string;
  score: number;
  flag: boolean;
  reasoning: string;
}

export type OverrideDecision = "approve" | "reject";

export interface HumanReview {
  id: string;
  decision: OverrideDecision;
  note: string;
  reviewer_id: string;
  reviewed_at: string;
}

export interface ReviewResult {
  id: string;
  final_verdict: Verdict;
  coordinator_reasoning: string;
  human_reviews: HumanReview[];
}

export interface TransactionDetail {
  id: string;
  user_id: string;
  amount: number;
  transaction_type: string;
  origin_balance_before: number;
  origin_balance_after: number;
  location_country: string;
  occurred_at: string;
  is_fraud_ground_truth: boolean | null;
  opinions: AgentOpinion[];
  review_result: ReviewResult | null;
}

export interface VerdictDistributionRow {
  verdict: Verdict;
  count: number;
}

export interface AgentAgreementPair {
  agents: [string, string];
  agree: number;
  total: number;
  rate: number;
}

export interface AgentAgreementRate {
  overall: { agree: number; disagree: number; total: number; rate: number };
  pairs: AgentAgreementPair[];
}

export interface VerdictTrendRow {
  date: string;
  allow: number;
  escalate: number;
  block: number;
}

export interface EvaluationSummary {
  n_test: number;
  n_fraud: number;
  n_legit: number;
  baseline_anomaly_only: {
    false_positive_rate: number;
    false_negative_rate: number;
  };
  multi_agent_pipeline: {
    strict_false_positive_rate: number;
    strict_false_negative_rate: number;
    broad_false_positive_rate: number;
    broad_false_negative_rate: number;
    escalation_rate: number;
  };
}
