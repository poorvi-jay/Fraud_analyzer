import type { TransactionListItem, Verdict } from "../types";
import { useEffect, useState } from "react";

import { Link } from "react-router-dom";
import { listTransactions } from "../api";

const VERDICT_OPTIONS: (Verdict | "")[] = ["", "allow", "escalate", "block"];
const PAGE_SIZE = 50;

export default function CaseQueue() {
  const [transactions, setTransactions] = useState<TransactionListItem[]>([]);
  const [verdict, setVerdict] = useState<Verdict | "">("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    listTransactions(verdict, PAGE_SIZE, 0)
      .then((rows) => {
        setTransactions(rows);
        setHasMore(rows.length === PAGE_SIZE);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [verdict]);

  function loadMore() {
    setLoadingMore(true);
    listTransactions(verdict, PAGE_SIZE, transactions.length)
      .then((rows) => {
        setTransactions((prev) => [...prev, ...rows]);
        setHasMore(rows.length === PAGE_SIZE);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingMore(false));
  }

  return (
    <div>
      <div className="toolbar">
        <h2>Case queue</h2>
        <label>
          Filter by verdict:{" "}
          <select value={verdict} onChange={(e) => setVerdict(e.target.value as Verdict | "")}>
            {VERDICT_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v === "" ? "all" : v}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p>Loading...</p>}
      {error && <p className="error">Failed to load transactions: {error}</p>}
      {!loading && !error && transactions.length === 0 && verdict && (
        <p>No {verdict} transactions in the queue.</p>
      )}
      {!loading && !error && transactions.length === 0 && !verdict && (
        <p>
          No transactions yet. Run <code>python ml/seed_demo_queue.py</code> against this backend to populate the
          queue.
        </p>
      )}

      {transactions.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Verdict</th>
              <th className="amt">Amount</th>
              <th>Type</th>
              <th>Location</th>
              <th>Occurred at</th>
              <th>User</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((t) => (
              <tr key={t.id}>
                <td>
                  <Link to={`/cases/${t.id}`}>
                    <span className={`badge badge-${t.final_verdict}`}>{t.final_verdict ?? "pending"}</span>
                  </Link>
                </td>
                <td className="amt">${t.amount.toFixed(2)}</td>
                <td>{t.transaction_type}</td>
                <td>{t.location_country}</td>
                <td>{new Date(t.occurred_at).toLocaleString()}</td>
                <td>{t.user_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {hasMore && (
        <p className="toolbar">
          <button type="button" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading..." : `Load more (showing ${transactions.length})`}
          </button>
        </p>
      )}
    </div>
  );
}
