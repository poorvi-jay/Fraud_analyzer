import { useState } from "react";
import type { FormEvent } from "react";
import { simulateRazorpayPayment } from "../api";
import type { RazorpaySimulateResponse } from "../types";

const TRAVEL_FREQUENCIES = ["never", "rare", "frequent"] as const;
const COUNTRIES = ["IN", "US", "GB", "DE", "FR", "SG", "AU", "CA"];

const AGENT_ORDER = ["anomaly_agent", "context_agent", "policy_agent"];
const AGENT_LABELS: Record<string, string> = {
  anomaly_agent: "Anomaly (ML)",
  context_agent: "Context (LLM)",
  policy_agent: "Policy (rules)",
};

const FEATURE_LABELS: Record<string, string> = {
  amount: "Amount",
  profile_relative_signals: "Profile-relative signals",
  balance_features: "Balance features (3 of 9)",
  transaction_type_signal: "Transaction-type signal (5 of 9)",
  observed_location_country: "Observed country",
};
const FEATURE_NOTES: Record<string, string> = {
  amount: "Razorpay quotes paise; converted to major units.",
  profile_relative_signals: "From our own user_profiles store -- Razorpay serves no spend history.",
  balance_features:
    "Razorpay carries no account balance -- these come from a balance context you supply, or the anomaly model is skipped entirely.",
  transaction_type_signal:
    "Razorpay's `method` is an instrument taxonomy; PaySim's `type` is a fund-flow taxonomy. Everything maps to PAYMENT, so these one-hots are constant.",
  observed_location_country:
    "No country field exists. `international` is mapped to an XX sentinel meaning 'not home', never a specific country.",
};

/** Realistic Razorpay test-mode Payment objects (GET /v1/payments/:id).
 *  Field names follow Razorpay's documented Payment entity; `amount` is in
 *  paise and `created_at` is a UNIX timestamp in seconds. */
const PRESETS: Record<string, { label: string; hint: string; payment: Record<string, unknown> }> = {
  domestic: {
    label: "Domestic card, captured",
    hint: "An ordinary ₹1,000 card payment from a known customer.",
    payment: {
      id: "pay_29QQoUBi66xm2f",
      entity: "payment",
      amount: 100000,
      currency: "INR",
      status: "captured",
      method: "card",
      international: false,
      captured: true,
      order_id: "order_GjCr5oKh4AVC51",
      customer_id: "cust_DitrYCFtCIokBO",
      email: "gaurav.kumar@example.com",
      contact: "9000090000",
      description: "Payment for Adventure Bag",
      fee: 2360,
      tax: 360,
      created_at: 1718000000,
    },
  },
  international: {
    label: "International, large amount",
    hint: "₹95,000 on an internationally-issued card. Watch the country sentinel in the warnings.",
    payment: {
      id: "pay_LkP2mQ8sTvBnXd",
      entity: "payment",
      amount: 9500000,
      currency: "INR",
      status: "captured",
      method: "card",
      international: true,
      captured: true,
      customer_id: "cust_DitrYCFtCIokBO",
      email: "gaurav.kumar@example.com",
      contact: "9000090000",
      created_at: 1718200000,
    },
  },
  failed: {
    label: "Failed UPI attempt",
    hint: "Not settled money movement -- the adapter flags that the agents were trained on completed transactions.",
    payment: {
      id: "pay_Nq7RtYw3ZxCvBm",
      entity: "payment",
      amount: 250000,
      currency: "INR",
      status: "failed",
      method: "upi",
      international: false,
      captured: false,
      customer_id: "cust_DitrYCFtCIokBO",
      vpa: "gaurav.kumar@okhdfcbank",
      error_code: "BAD_REQUEST_ERROR",
      error_description: "Payment failed due to insufficient funds",
      created_at: 1718300000,
    },
  },
};

type Status = "idle" | "running" | "done" | "error";

export default function RazorpayPlayground() {
  const [paymentJson, setPaymentJson] = useState(JSON.stringify(PRESETS.domestic.payment, null, 2));
  const [hasBalances, setHasBalances] = useState(true);
  const [balanceBefore, setBalanceBefore] = useState("5000");
  const [balanceAfter, setBalanceAfter] = useState("4000");
  const [homeCountry, setHomeCountry] = useState("IN");
  const [typicalAmount, setTypicalAmount] = useState("900");
  const [travelFrequency, setTravelFrequency] =
    useState<(typeof TRAVEL_FREQUENCIES)[number]>("rare");
  const [accountAgeDays, setAccountAgeDays] = useState("400");

  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<RazorpaySimulateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function loadPreset(key: keyof typeof PRESETS) {
    setPaymentJson(JSON.stringify(PRESETS[key].payment, null, 2));
    setResult(null);
    setStatus("idle");
    setError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);

    let payment: Record<string, unknown>;
    try {
      payment = JSON.parse(paymentJson);
    } catch {
      setError("That isn't valid JSON. Paste a Razorpay Payment object.");
      setStatus("error");
      return;
    }

    setStatus("running");
    try {
      const response = await simulateRazorpayPayment({
        payment,
        profile: {
          home_country: homeCountry,
          typical_transaction_amount: Number(typicalAmount),
          travel_frequency: travelFrequency,
          account_age_days: Number(accountAgeDays),
        },
        ...(hasBalances
          ? {
              balance_context: {
                origin_balance_before: Number(balanceBefore),
                origin_balance_after: Number(balanceAfter),
              },
            }
          : { allow_degraded: true }),
      });
      setResult(response);
      setStatus("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scoring failed.");
      setStatus("error");
    }
  }

  const busy = status === "running";
  const opinions = result
    ? [...result.opinions].sort(
        (a, b) => AGENT_ORDER.indexOf(a.agent_name) - AGENT_ORDER.indexOf(b.agent_name)
      )
    : [];

  return (
    <div className="playground">
      <section>
        <h2>Razorpay input</h2>
        <p className="subtle">
          Paste a Razorpay test-mode Payment object and score it through the same four agents. The
          adapter maps it onto the internal schema without inventing anything it cannot observe --
          so this screen shows you what survived the mapping alongside the verdict, because the
          verdict is not safe to read without it.
        </p>
      </section>

      <form className="playground-form" onSubmit={handleSubmit}>
        <fieldset disabled={busy}>
          <legend>Razorpay payment object</legend>
          <div className="preset-row">
            {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).map((key) => (
              <button
                key={key}
                type="button"
                className="link-button"
                onClick={() => loadPreset(key)}
              >
                {PRESETS[key].label}
              </button>
            ))}
          </div>
          <textarea
            className="json-input"
            rows={16}
            spellCheck={false}
            value={paymentJson}
            onChange={(e) => setPaymentJson(e.target.value)}
          />
          <p className="subtle">
            <code>amount</code> is in paise (Razorpay's minor unit) and <code>created_at</code> is a
            UNIX timestamp in seconds. Extra fields are accepted and ignored.
          </p>
        </fieldset>

        <fieldset disabled={busy}>
          <legend>Account balances (not carried by Razorpay)</legend>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={!hasBalances}
              onChange={(e) => setHasBalances(!e.target.checked)}
            />
            <span>
              I don't have balance data for this account &mdash; run in <strong>degraded mode</strong>
            </span>
          </label>
          <p className="subtle">
            Razorpay's Payment object has no balance field, and balances are 3 of the anomaly
            model's 9 features. Supply them from your own ledger for full-fidelity scoring, or tick
            the box and the model is skipped entirely rather than scored on invented values.
          </p>
          {hasBalances && (
            <div className="form-grid">
              <label>
                Balance before (₹)
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={balanceBefore}
                  onChange={(e) => setBalanceBefore(e.target.value)}
                  required
                />
              </label>
              <label>
                Balance after (₹)
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={balanceAfter}
                  onChange={(e) => setBalanceAfter(e.target.value)}
                  required
                />
              </label>
            </div>
          )}
        </fieldset>

        <fieldset disabled={busy}>
          <legend>Customer profile (maintained by us, not by Razorpay)</legend>
          <div className="form-grid">
            <label>
              Home country
              <select value={homeCountry} onChange={(e) => setHomeCountry(e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Typical spend (₹)
              <input
                type="number"
                min="1"
                step="0.01"
                value={typicalAmount}
                onChange={(e) => setTypicalAmount(e.target.value)}
                required
              />
            </label>
            <label>
              Travel frequency
              <select
                value={travelFrequency}
                onChange={(e) =>
                  setTravelFrequency(e.target.value as (typeof TRAVEL_FREQUENCIES)[number])
                }
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
                value={accountAgeDays}
                onChange={(e) => setAccountAgeDays(e.target.value)}
                required
              />
            </label>
          </div>
          <p className="subtle">
            Razorpay exposes no per-customer spend history, so{" "}
            <code>amount_to_typical_ratio</code> has to come from a profile we keep ourselves.
          </p>
        </fieldset>

        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Scoring..." : "Score this payment"}
        </button>
      </form>

      {result && (
        <section className="investigation">
          {!result.anomaly_scored && (
            <p className="degraded-banner">
              Degraded run &mdash; the anomaly model was not executed. This verdict rests on the
              context and policy agents alone.
            </p>
          )}

          <h3>Agent opinions</h3>
          <div className="opinions">
            {opinions.map((opinion) => {
              const unscored = opinion.agent_name === "anomaly_agent" && !result.anomaly_scored;
              return (
                <div
                  key={opinion.agent_name}
                  className={`opinion-card ${unscored ? "unscored" : opinion.flag ? "flagged" : ""}`}
                >
                  <h4>{AGENT_LABELS[opinion.agent_name] ?? opinion.agent_name}</h4>
                  <p className="score">
                    {/* Never render 0.00 / clear for a model that never ran -- that is
                        precisely the misreading the adapter exists to prevent. */}
                    {unscored ? "not scored" : `score ${opinion.score.toFixed(2)} · ${opinion.flag ? "flagged" : "clear"}`}
                  </p>
                  <p>{opinion.reasoning}</p>
                </div>
              );
            })}
          </div>

          <h3>What survived the mapping</h3>
          <table className="kv feature-availability">
            <tbody>
              {Object.entries(result.feature_availability).map(([key, available]) => (
                <tr key={key}>
                  <td>{FEATURE_LABELS[key] ?? key}</td>
                  <td className={available ? "" : "unavailable"}>
                    {available ? "available" : "unavailable"}
                  </td>
                  <td className="subtle">{FEATURE_NOTES[key] ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {result.adapter_warnings.length > 0 && (
            <>
              <h3>Adapter notes ({result.adapter_warnings.length})</h3>
              <ul className="adapter-warnings">
                {result.adapter_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </>
          )}

          <div className="coordinator">
            <h3>
              Coordinator verdict{" "}
              <span className={`badge badge-${result.final_verdict}`} style={{ marginLeft: 8 }}>
                {result.final_verdict}
              </span>
            </h3>
            <p>{result.coordinator_reasoning}</p>
            <p className="subtle">
              Mapped to customer <code>{result.user_id}</code>.
              {!result.anomaly_scored &&
                " Because the anomaly opinion carries no flag, this verdict could only have been `allow` or `escalate` -- a degraded run can never auto-block."}
            </p>
          </div>
        </section>
      )}
    </div>
  );
}
