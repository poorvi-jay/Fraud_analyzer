import { useState } from "react";
import type { FormEvent } from "react";
import { simulateTransaction } from "../api";
import type { SimulateResponse } from "../types";

const TRANSACTION_TYPES = ["PAYMENT", "CASH_OUT", "CASH_IN", "TRANSFER", "DEBIT"];
const COUNTRIES = ["US", "GB", "DE", "FR", "IN", "BR", "NG", "SG", "AU", "CA"];
const TRAVEL_FREQUENCIES = ["never", "rare", "frequent"] as const;

const AGENT_ORDER = ["anomaly_agent", "context_agent", "policy_agent"];
const AGENT_LABELS: Record<string, string> = {
  anomaly_agent: "Anomaly (ML)",
  context_agent: "Context (LLM)",
  policy_agent: "Policy (rules)",
};
const AGENT_BLURBS: Record<string, string> = {
  anomaly_agent: "Scores the transaction on its own statistics -- amount, balance movement, type. Has no idea who the user is.",
  context_agent: "Checks whether this fits the profile you built below -- typical spend, home country, travel history.",
  policy_agent: "Runs deterministic compliance rules -- mule-pattern drains, large-amount reporting, new-account abuse.",
};

const REVEAL_DELAY_MS = 900;

type Status = "idle" | "investigating" | "done" | "error";

interface FormState {
  amount: string;
  transactionType: string;
  balanceBefore: string;
  balanceAfter: string;
  location: string;
  homeCountry: string;
  typicalAmount: string;
  travelFrequency: (typeof TRAVEL_FREQUENCIES)[number];
  accountAgeDays: string;
}

const INITIAL_FORM: FormState = {
  amount: "9000",
  transactionType: "TRANSFER",
  balanceBefore: "9500",
  balanceAfter: "500",
  location: "NG",
  homeCountry: "US",
  typicalAmount: "250",
  travelFrequency: "never",
  accountAgeDays: "5",
};

export default function Playground() {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<SimulateResponse | null>(null);
  const [revealCount, setRevealCount] = useState(0);
  const [verdictRevealed, setVerdictRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function scheduleReveal(response: SimulateResponse) {
    setResult(response);
    AGENT_ORDER.forEach((_, i) => {
      setTimeout(() => setRevealCount((c) => Math.max(c, i + 1)), REVEAL_DELAY_MS * (i + 1));
    });
    setTimeout(() => {
      setVerdictRevealed(true);
      setStatus("done");
    }, REVEAL_DELAY_MS * (AGENT_ORDER.length + 1));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setRevealCount(0);
    setVerdictRevealed(false);
    setStatus("investigating");
    try {
      const response = await simulateTransaction({
        profile: {
          home_country: form.homeCountry,
          typical_transaction_amount: Number(form.typicalAmount),
          travel_frequency: form.travelFrequency,
          account_age_days: Number(form.accountAgeDays),
        },
        amount: Number(form.amount),
        transaction_type: form.transactionType,
        origin_balance_before: Number(form.balanceBefore),
        origin_balance_after: Number(form.balanceAfter),
        location_country: form.location,
      });
      scheduleReveal(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Investigation failed.");
      setStatus("error");
    }
  }

  const opinions = result
    ? [...result.opinions].sort((a, b) => AGENT_ORDER.indexOf(a.agent_name) - AGENT_ORDER.indexOf(b.agent_name))
    : [];
  const busy = status === "investigating";

  return (
    <div className="playground">
      <section>
        <h2>Try it yourself</h2>
        <p className="subtle">
          Build a transaction and a user profile from scratch, then watch the anomaly, context, and policy agents
          investigate it independently before the coordinator reconciles their opinions. This runs the real
          pipeline -- nothing you submit here is saved to the database.
        </p>
      </section>

      <form className="playground-form" onSubmit={handleSubmit}>
        <fieldset disabled={busy}>
          <legend>Transaction</legend>
          <div className="form-grid">
            <label>
              Amount ($)
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.amount}
                onChange={(e) => update("amount", e.target.value)}
                required
              />
            </label>
            <label>
              Type
              <select value={form.transactionType} onChange={(e) => update("transactionType", e.target.value)}>
                {TRANSACTION_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Balance before ($)
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.balanceBefore}
                onChange={(e) => update("balanceBefore", e.target.value)}
                required
              />
            </label>
            <label>
              Balance after ($)
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.balanceAfter}
                onChange={(e) => update("balanceAfter", e.target.value)}
                required
              />
            </label>
            <label>
              Transaction location
              <select value={form.location} onChange={(e) => update("location", e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </fieldset>

        <fieldset disabled={busy}>
          <legend>User profile (built from scratch)</legend>
          <div className="form-grid">
            <label>
              Home country
              <select value={form.homeCountry} onChange={(e) => update("homeCountry", e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Typical spend ($)
              <input
                type="number"
                min="1"
                step="0.01"
                value={form.typicalAmount}
                onChange={(e) => update("typicalAmount", e.target.value)}
                required
              />
            </label>
            <label>
              Travel frequency
              <select
                value={form.travelFrequency}
                onChange={(e) => update("travelFrequency", e.target.value as FormState["travelFrequency"])}
              >
                {TRAVEL_FREQUENCIES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Account age (days)
              <input
                type="number"
                min="0"
                step="1"
                value={form.accountAgeDays}
                onChange={(e) => update("accountAgeDays", e.target.value)}
                required
              />
            </label>
          </div>
        </fieldset>

        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Investigating..." : "Investigate transaction"}
        </button>
      </form>

      {status !== "idle" && !error && (
        <section className="investigation">
          <h3>Investigation</h3>
          <div className="opinions">
            {AGENT_ORDER.map((name, i) => {
              const opinion = opinions.find((o) => o.agent_name === name);
              const revealed = i < revealCount && opinion;
              return (
                <div key={name} className={`opinion-card ${revealed ? (opinion!.flag ? "flagged" : "") : "pending"}`}>
                  <h4>{AGENT_LABELS[name]}</h4>
                  {revealed ? (
                    <>
                      <p className="score">
                        score {opinion!.score.toFixed(2)} &middot; {opinion!.flag ? "flagged" : "clear"}
                      </p>
                      <p>{opinion!.reasoning}</p>
                    </>
                  ) : (
                    <p className="subtle">
                      {i === revealCount ? "Analyzing..." : "Waiting its turn..."} {AGENT_BLURBS[name]}
                    </p>
                  )}
                </div>
              );
            })}
          </div>

          {verdictRevealed && result && (
            <div className="coordinator">
              <h3>
                Coordinator verdict{" "}
                <span className={`badge badge-${result.final_verdict}`} style={{ marginLeft: 8 }}>
                  {result.final_verdict}
                </span>
              </h3>
              <p>{result.coordinator_reasoning}</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
