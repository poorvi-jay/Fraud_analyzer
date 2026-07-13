import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getTransaction } from "../api";
import type { TransactionDetail } from "../types";

const AGENT_ORDER = ["anomaly_agent", "context_agent", "policy_agent"];
const AGENT_LABELS: Record<string, string> = {
  anomaly_agent: "Anomaly (ML)",
  context_agent: "Context (LLM)",
  policy_agent: "Policy (rules)",
};

export default function CaseDetail() {
  const { id } = useParams<{ id: string }>();
  const [txn, setTxn] = useState<TransactionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getTransaction(id)
      .then(setTxn)
      .catch((e) => setError(e.message));
  }, [id]);

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
        </div>
      )}
    </div>
  );
}
