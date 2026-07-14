import type {
  AgentAgreementRate,
  EvaluationSummary,
  OverrideDecision,
  ReviewResult,
  TransactionDetail,
  TransactionListItem,
  Verdict,
  VerdictDistributionRow,
  VerdictTrendRow,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body wasn't JSON -- fall back to statusText
    }
    throw new Error(detail);
  }
  return resp.json();
}

export function listTransactions(verdict?: Verdict | ""): Promise<TransactionListItem[]> {
  const query = verdict ? `?verdict=${verdict}` : "";
  return request(`/transactions${query}`);
}

export function getTransaction(id: string): Promise<TransactionDetail> {
  return request(`/transactions/${id}`);
}

export function overrideReview(
  reviewResultId: string,
  decision: OverrideDecision,
  note: string,
  accessToken: string
): Promise<ReviewResult> {
  return request(`/reviews/${reviewResultId}/override`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ decision, note }),
  });
}

export function getVerdictDistribution(): Promise<VerdictDistributionRow[]> {
  return request("/analytics/verdict-distribution");
}

export function getAgentAgreementRate(): Promise<AgentAgreementRate> {
  return request("/analytics/agent-agreement-rate");
}

export function getVerdictTrend(): Promise<VerdictTrendRow[]> {
  return request("/analytics/verdict-trend");
}

export function getEvaluationSummary(): Promise<EvaluationSummary> {
  return request("/analytics/evaluation-summary");
}
