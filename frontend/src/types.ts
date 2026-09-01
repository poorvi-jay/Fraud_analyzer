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

export interface InlineProfile {
  home_country: string;
  typical_transaction_amount: number;
  travel_frequency: "never" | "rare" | "frequent";
  account_age_days: number;
}

export interface SimulateRequest {
  profile: InlineProfile;
  amount: number;
  transaction_type: string;
  origin_balance_before: number;
  origin_balance_after: number;
  location_country: string;
}

export interface SimulateResponse {
  opinions: AgentOpinion[];
  final_verdict: Verdict;
  coordinator_reasoning: string;
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

// --- Razorpay ingestion (see backend/app/adapters/razorpay_adapter.py) ---

export interface RazorpayBalanceContext {
  origin_balance_before: number;
  origin_balance_after: number;
}

export interface RazorpaySimulateRequest {
  /** A raw Razorpay Payment object. Deliberately untyped: the adapter
   *  accepts extra fields, and the point of this screen is to paste a real
   *  payload unmodified. */
  payment: Record<string, unknown>;
  profile?: InlineProfile;
  balance_context?: RazorpayBalanceContext;
  allow_degraded?: boolean;
}

export interface RazorpaySimulateResponse {
  opinions: AgentOpinion[];
  final_verdict: Verdict;
  coordinator_reasoning: string;
  user_id: string;
  /** False when the anomaly model was skipped for lack of balance inputs.
   *  When false, that agent's score is NOT a risk reading -- see the note
   *  rendered next to it. */
  anomaly_scored: boolean;
  feature_availability: Record<string, boolean>;
  adapter_warnings: string[];
}
