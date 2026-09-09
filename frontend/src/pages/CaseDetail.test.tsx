import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CaseDetail from "./CaseDetail";
import * as api from "../api";
import * as auth from "../auth";
import type { TransactionDetail } from "../types";

const CASE: TransactionDetail = {
  id: "abcd1234-0000-0000-0000-000000000000",
  user_id: "C_TEST",
  amount: 8000,
  transaction_type: "TRANSFER",
  origin_balance_before: 8000,
  origin_balance_after: 0,
  location_country: "FR",
  occurred_at: "2024-06-15T10:00:00",
  is_fraud_ground_truth: null,
  opinions: [
    { agent_name: "policy_agent", score: 1, flag: true, reasoning: "Reporting threshold exceeded." },
    { agent_name: "anomaly_agent", score: 0.91, flag: true, reasoning: "Full balance drain." },
    { agent_name: "context_agent", score: 0.85, flag: true, reasoning: "Never travels abroad." },
  ],
  review_result: {
    id: "rr-1",
    final_verdict: "escalate",
    coordinator_reasoning: "Anomaly and context disagree.",
    human_reviews: [],
  },
};

function renderCase() {
  return render(
    <MemoryRouter initialEntries={[`/case/${CASE.id}`]}>
      <Routes>
        <Route path="/case/:id" element={<CaseDetail />} />
      </Routes>
    </MemoryRouter>
  );
}

function stubSession(session: { access_token: string } | null) {
  vi.spyOn(auth, "useAuth").mockReturnValue({
    session,
    signIn: vi.fn(),
    signOut: vi.fn(),
    loading: false,
  } as unknown as ReturnType<typeof auth.useAuth>);
}

describe("CaseDetail", () => {
  beforeEach(() => {
    vi.spyOn(api, "getTransaction").mockResolvedValue(structuredClone(CASE));
    stubSession(null);
  });

  it("orders agent opinions anomaly, context, policy regardless of API order", async () => {
    renderCase();

    const headings = await screen.findAllByRole("heading", { level: 4 });
    expect(headings.map((h) => h.textContent)).toEqual([
      "Anomaly (ML)",
      "Context (LLM)",
      "Policy (rules)",
    ]);
  });

  it("offers a PDF export pointing at the backend report endpoint", async () => {
    renderCase();

    const link = await screen.findByRole("link", { name: /export pdf/i });
    expect(link).toHaveAttribute("href", expect.stringContaining(`/transactions/${CASE.id}/report.pdf`));
    // A real download attribute, so the browser names the file rather than
    // rendering the PDF into the tab and losing the filename.
    expect(link).toHaveAttribute("download", "case-abcd1234.pdf");
  });

  it("asks an anonymous visitor to sign in rather than showing the override form", async () => {
    renderCase();

    expect(await screen.findByText(/to review this case/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit override/i })).not.toBeInTheDocument();
  });

  it("submits an override with the reviewer's token and reloads the case", async () => {
    stubSession({ access_token: "token-123" });
    const override = vi.spyOn(api, "overrideReview").mockResolvedValue({
      id: "rr-1",
      final_verdict: "escalate",
      coordinator_reasoning: "Anomaly and context disagree.",
      human_reviews: [],
    });
    renderCase();

    await userEvent.type(await screen.findByLabelText(/note/i), "Verified by phone.");
    await userEvent.click(screen.getByRole("button", { name: /submit override/i }));

    await waitFor(() =>
      expect(override).toHaveBeenCalledWith("rr-1", "approve", "Verified by phone.", "token-123")
    );
    // Reloaded so the new entry appears without a manual refresh.
    expect(api.getTransaction).toHaveBeenCalledTimes(2);
  });

  it("shows the override error instead of silently failing", async () => {
    stubSession({ access_token: "expired" });
    vi.spyOn(api, "overrideReview").mockRejectedValue(new Error("Not authenticated"));
    renderCase();

    await userEvent.type(await screen.findByLabelText(/note/i), "n");
    await userEvent.click(screen.getByRole("button", { name: /submit override/i }));

    expect(await screen.findByText("Not authenticated")).toBeInTheDocument();
  });

  it("hides the override form on a case that was not escalated", async () => {
    stubSession({ access_token: "token-123" });
    vi.spyOn(api, "getTransaction").mockResolvedValue({
      ...structuredClone(CASE),
      review_result: {
        id: "rr-2",
        final_verdict: "block",
        coordinator_reasoning: "Policy flag overrides.",
        human_reviews: [],
      },
    });
    renderCase();

    expect(await screen.findByText("Policy flag overrides.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit override/i })).not.toBeInTheDocument();
  });
});
