import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Analytics from "./Analytics";
import * as api from "../api";
import type { AgentAgreementRate, OverrideOutcomes } from "../types";

const AGREEMENT: AgentAgreementRate = {
  overall: { agree: 8, disagree: 2, total: 10, rate: 0.8 },
  pairs: [
    { agents: ["anomaly_agent", "context_agent"], agree: 7, total: 10, rate: 0.7 },
    { agents: ["anomaly_agent", "policy_agent"], agree: 9, total: 10, rate: 0.9 },
    { agents: ["context_agent", "policy_agent"], agree: 8, total: 10, rate: 0.8 },
  ],
};

const OVERRIDES: OverrideOutcomes = {
  escalated_total: 10,
  reviewed: 4,
  pending: 6,
  review_rate: 0.4,
  total_overrides: 5,
  decisions: { approve: 3, reject: 1 },
  approve_rate: 0.75,
};

function stubEndpoints(overrides: Partial<OverrideOutcomes> = {}) {
  vi.spyOn(api, "getVerdictDistribution").mockResolvedValue([
    { verdict: "allow", count: 30 },
    { verdict: "escalate", count: 10 },
    { verdict: "block", count: 5 },
  ]);
  vi.spyOn(api, "getAgentAgreementRate").mockResolvedValue(AGREEMENT);
  vi.spyOn(api, "getVerdictTrend").mockResolvedValue([
    { date: "2024-06-15", allow: 3, escalate: 1, block: 1 },
  ]);
  vi.spyOn(api, "getOverrideOutcomes").mockResolvedValue({ ...OVERRIDES, ...overrides });
  vi.spyOn(api, "getAgentFlagTrend").mockResolvedValue([
    { date: "2024-06-15", anomaly_agent: 0.2, context_agent: 0.5, policy_agent: 0.1 },
  ]);
  // The evaluation summary 404s until ml/evaluate_baseline.py has run; the
  // dashboard has to render without it.
  vi.spyOn(api, "getEvaluationSummary").mockRejectedValue(new Error("404"));
}

describe("Analytics", () => {
  beforeEach(() => stubEndpoints());

  it("renders headline stats once every endpoint resolves", async () => {
    render(<Analytics />);

    expect(await screen.findByText("Analytics")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument(); // 30 + 10 + 5 reviewed
    expect(screen.getByText("80.0%")).toBeInTheDocument(); // agent agreement rate
  });

  it("shows human review outcomes with the pending count", async () => {
    render(<Analytics />);

    expect(await screen.findByText("Human review outcomes")).toBeInTheDocument();
    expect(screen.getByText("Awaiting review")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument(); // cleared on review
  });

  it("says so plainly when no case has been reviewed yet, instead of charting a zero", async () => {
    vi.restoreAllMocks();
    stubEndpoints({ reviewed: 0, pending: 10, review_rate: 0, decisions: { approve: 0, reject: 0 }, approve_rate: 0 });
    render(<Analytics />);

    expect(await screen.findByText(/No escalated case has been reviewed yet/)).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("labels the review data as a proxy rather than ground truth", async () => {
    render(<Analytics />);

    // This caveat is load-bearing: only escalated cases are ever reviewed,
    // so the approve rate is not a false-positive rate and must not read
    // like one.
    expect(await screen.findByText(/proxy, not a label/)).toBeInTheDocument();
  });

  it("renders the agent flag trend chart", async () => {
    render(<Analytics />);

    expect(await screen.findByText("Agent flag rate over time")).toBeInTheDocument();
    expect(screen.getByText("Anomaly (ML)")).toBeInTheDocument();
    expect(screen.getByText("Policy (rules)")).toBeInTheDocument();
  });

  it("surfaces a failure instead of spinning forever", async () => {
    vi.restoreAllMocks();
    stubEndpoints();
    vi.spyOn(api, "getOverrideOutcomes").mockRejectedValue(new Error("backend is down"));
    render(<Analytics />);

    await waitFor(() =>
      expect(screen.getByText(/Failed to load analytics: backend is down/)).toBeInTheDocument()
    );
  });
});
