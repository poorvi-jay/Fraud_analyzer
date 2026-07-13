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

export interface ReviewResult {
  final_verdict: Verdict;
  coordinator_reasoning: string;
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
