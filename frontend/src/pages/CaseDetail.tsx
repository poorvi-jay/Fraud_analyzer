import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { getTransaction, overrideReview } from "../api";
import { useAuth } from "../auth";
import type { OverrideDecision, TransactionDetail } from "../types";

const AGENT_ORDER = ["anomaly_agent", "context_agent", "policy_agent"];
const AGENT_LABELS: Record<string, string> = {
  anomaly_agent: "Anomaly (ML)",
  context_agent: "Context (LLM)",
  policy_agent: "Policy (rules)",
};

function OverrideForm({ reviewResultId, onOverridden }: { reviewResultId: string; onOverridden: () => void }) {
  const { session } = useAuth();
  const [decision, setDecision] = useState<OverrideDecision>("approve");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!session) return;
    setError(null);
    setSubmitting(true);
    try {
      await overrideReview(reviewResultId, decision, note, session.access_token);
      setNote("");
      onOverridden();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Override failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="override-form" onSubmit={handleSubmit}>
      <label>
        Decision
        <select value={decision} onChange={(e) => setDecision(e.target.value as OverrideDecision)}>
          <option value="approve">Approve (allow)</option>
          <option value="reject">Reject (block)</option>
        </select>
      </label>
      <label>
        Note
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? "Submitting..." : "Submit override"}
      </button>
    </form>
  );
}

export default function CaseDetail() {
  const { id } = useParams<{ id: string }>();
  const { session } = useAuth();
  const [txn, setTxn] = useState<TransactionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    if (!id) return;
    getTransaction(id)
      .then(setTxn)
      .catch((e) => setError(e.message));
  }

  useEffect(reload, [id]);

  if (error) return <p className="error">Failed to load case: {error}</p>;
  if (!txn) return <p>Loading...</p>;

  const opinions = [...txn.opinions].sort(
    (a, b) => AGENT_ORDER.indexOf(a.agent_name) - AGENT_ORDER.indexOf(b.agent_name)
  );

  return (
    <div>
      <p>
        <Link to="/">&larr; back to queue</Link>
      </p>
      <h2>
        Case {txn.id.slice(0, 8)}
        {txn.review_result && (
          <span className={`badge badge-${txn.review_result.final_verdict}`} style={{ marginLeft: 12 }}>
            {txn.review_result.final_verdict}
          </span>
        )}
      </h2>

      <table className="kv">
        <tbody>
          <tr>
            <td>User</td>
            <td>{txn.user_id}</td>
          </tr>
          <tr>
            <td>Amount</td>
            <td>${txn.amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td>Type</td>
            <td>{txn.transaction_type}</td>
          </tr>
          <tr>
            <td>Balance before &rarr; after</td>
            <td>
              ${txn.origin_balance_before.toFixed(2)} &rarr; ${txn.origin_balance_after.toFixed(2)}
            </td>
          </tr>
          <tr>
            <td>Location</td>
            <td>{txn.location_country}</td>
          </tr>
          <tr>
            <td>Occurred at</td>
            <td>{new Date(txn.occurred_at).toLocaleString()}</td>
          </tr>
          {txn.is_fraud_ground_truth !== null && (
            <tr>
              <td>Ground truth (eval set only)</td>
              <td>{txn.is_fraud_ground_truth ? "fraud" : "legitimate"}</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3>Agent opinions</h3>
      <div className="opinions">
        {opinions.map((o) => (
          <div key={o.agent_name} className={`opinion-card ${o.flag ? "flagged" : ""}`}>
            <h4>{AGENT_LABELS[o.agent_name] ?? o.agent_name}</h4>
            <p className="score">
              score {o.score.toFixed(2)} &middot; {o.flag ? "flagged" : "clear"}
            </p>
            <p>{o.reasoning}</p>
          </div>
        ))}
      </div>

      {txn.review_result && (
        <div className="coordinator">
          <h3>Coordinator reasoning</h3>
          <p>{txn.review_result.coordinator_reasoning}</p>

          {txn.review_result.final_verdict === "escalate" && (
            <>
              <h3>Reviewer override</h3>
              {session ? (
                <OverrideForm reviewResultId={txn.review_result.id} onOverridden={reload} />
              ) : (
                <p className="subtle">
                  <Link to="/login">Sign in</Link> to review this case.
                </p>
              )}
            </>
          )}

          {txn.review_result.human_reviews.length > 0 && (
            <div className="human-reviews">
              <h3>Review history</h3>
              {txn.review_result.human_reviews.map((r) => (
                <div key={r.id} className="human-review-entry">
                  <p className="meta">
                    <span className={r.decision === "reject" ? "decision-reject" : undefined}>{r.decision}</span>
                    {" · "}
                    {new Date(r.reviewed_at).toLocaleString()}
                  </p>
                  <p>{r.note}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
