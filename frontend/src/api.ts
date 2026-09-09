import type {
  AgentAgreementRate,
  AgentFlagTrendRow,
  EvaluationSummary,
  OverrideOutcomes,
  OverrideDecision,
  ReviewResult,
  RazorpaySimulateRequest,
  RazorpaySimulateResponse,
  SimulateRequest,
  SimulateResponse,
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

export function listTransactions(
  verdict?: Verdict | "",
  limit = 50,
  offset = 0
): Promise<TransactionListItem[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (verdict) params.set("verdict", verdict);
  return request(`/transactions?${params}`);
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

export function getOverrideOutcomes(): Promise<OverrideOutcomes> {
  return request("/analytics/override-outcomes");
}

export function getAgentFlagTrend(): Promise<AgentFlagTrendRow[]> {
  return request("/analytics/agent-flag-trend");
}

/** URL of the server-rendered PDF case file. Returned as a URL rather than
 *  fetched: the browser's own download handling gives a progress indicator
 *  and a real filename from Content-Disposition, both of which we'd have to
 *  reimplement badly to hand it a blob instead. */
export function caseReportUrl(transactionId: string): string {
  return `${API_BASE_URL}/transactions/${encodeURIComponent(transactionId)}/report.pdf`;
}

export function simulateTransaction(payload: SimulateRequest): Promise<SimulateResponse> {
  return request("/transactions/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function simulateRazorpayPayment(
  payload: RazorpaySimulateRequest
): Promise<RazorpaySimulateResponse> {
  return request("/transactions/razorpay/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
