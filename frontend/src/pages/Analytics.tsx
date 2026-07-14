import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getAgentAgreementRate, getEvaluationSummary, getVerdictDistribution, getVerdictTrend } from "../api";
import type { AgentAgreementRate, EvaluationSummary, VerdictDistributionRow, VerdictTrendRow } from "../types";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const VERDICT_LABELS: Record<string, string> = { allow: "Allow", escalate: "Escalate", block: "Block" };

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

export default function Analytics() {
  const [distribution, setDistribution] = useState<VerdictDistributionRow[] | null>(null);
  const [agreement, setAgreement] = useState<AgentAgreementRate | null>(null);
  const [trend, setTrend] = useState<VerdictTrendRow[] | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluationSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getVerdictDistribution(), getAgentAgreementRate(), getVerdictTrend()])
      .then(([d, a, t]) => {
        setDistribution(d);
        setAgreement(a);
        setTrend(t);
      })
      .catch((e) => setError(e.message));
    // Evaluation summary is a separate, optional fetch -- it 404s until
    // ml/evaluate_baseline.py has been run, which shouldn't block the rest
    // of the dashboard from rendering.
    getEvaluationSummary()
      .then(setEvaluation)
      .catch(() => setEvaluation(null));
  }, []);

  if (error) return <p className="error">Failed to load analytics: {error}</p>;
  if (!distribution || !agreement || !trend) return <p>Loading...</p>;

  const colors = {
    allow: cssVar("--ink"),
    escalate: cssVar("--red-light"),
    block: cssVar("--red"),
    grid: cssVar("--rule"),
  };

  const distributionByVerdict = Object.fromEntries(distribution.map((r) => [r.verdict, r.count]));
  const totalReviewed = distribution.reduce((sum, r) => sum + r.count, 0);

  return (
    <div>
      <h2>Analytics</h2>

      <div className="stat-row">
        <div className="stat-card">
          <div className="label">Transactions reviewed</div>
          <div className="value">{totalReviewed}</div>
        </div>
        <div className="stat-card">
          <div className="label">Escalated</div>
          <div className="value flagged">{distributionByVerdict.escalate ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="label">Agent agreement rate</div>
          <div className="value">{pct(agreement.overall.rate)}</div>
        </div>
        {evaluation && (
          <div className="stat-card">
            <div className="label">Pipeline vs. anomaly-only FN rate</div>
            <div className="value">
              {pct(evaluation.multi_agent_pipeline.strict_false_negative_rate)}{" "}
              <span className="subtle">vs {pct(evaluation.baseline_anomaly_only.false_negative_rate)}</span>
            </div>
          </div>
        )}
      </div>

      <div className="chart-card">
        <h3>Verdict distribution</h3>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={distribution} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke={colors.grid} vertical={false} />
            <XAxis
              dataKey="verdict"
              tickFormatter={(v: string) => VERDICT_LABELS[v] ?? v}
              tick={{ fontSize: 12 }}
            />
            <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
            <Tooltip labelFormatter={(v: string) => VERDICT_LABELS[v] ?? v} />
            <Bar dataKey="count" name="Transactions">
              {distribution.map((row) => (
                <Cell key={row.verdict} fill={colors[row.verdict as keyof typeof colors]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-card">
        <h3>Verdict volume over time</h3>
        <p className="subtle">
          Live demo transactions have no ground truth, so this shows verdict counts by day, not a false-positive
          rate.
        </p>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={trend} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke={colors.grid} vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} />
            <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
            <Tooltip />
            <Legend formatter={(v: string) => VERDICT_LABELS[v] ?? v} />
            <Bar dataKey="allow" stackId="v" name="allow" fill={colors.allow} />
            <Bar dataKey="escalate" stackId="v" name="escalate" fill={colors.escalate} />
            <Bar dataKey="block" stackId="v" name="block" fill={colors.block} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-card">
        <h3>Agent pairwise agreement</h3>
        <p className="subtle">
          Escalation is defined as anomaly/context disagreement (see coordinator_agent.py), so that pair's rate is
          the direct empirical check on the pipeline's core thesis.
        </p>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart
            layout="vertical"
            data={agreement.pairs.map((p) => ({ label: p.agents.join(" vs "), rate: p.rate }))}
            margin={{ top: 8, right: 24, left: 24, bottom: 8 }}
          >
            <CartesianGrid stroke={colors.grid} horizontal={false} />
            <XAxis type="number" domain={[0, 1]} tickFormatter={pct} tick={{ fontSize: 12 }} />
            <YAxis type="category" dataKey="label" width={180} tick={{ fontSize: 12 }} />
            <Tooltip formatter={(value: number) => pct(value)} />
            <Bar dataKey="rate" name="Agreement rate" fill={colors.allow} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {evaluation && (
        <div className="chart-card">
          <h3>Baseline vs. multi-agent pipeline</h3>
          <table className="kv">
            <tbody>
              <tr>
                <td>Test set size</td>
                <td>
                  {evaluation.n_test} transactions ({evaluation.n_fraud} fraud, {evaluation.n_legit} legit)
                </td>
              </tr>
              <tr>
                <td>False negative rate</td>
                <td>
                  anomaly-only {pct(evaluation.baseline_anomaly_only.false_negative_rate)} &rarr; pipeline{" "}
                  {pct(evaluation.multi_agent_pipeline.strict_false_negative_rate)}
                </td>
              </tr>
              <tr>
                <td>False positive rate</td>
                <td>
                  anomaly-only {pct(evaluation.baseline_anomaly_only.false_positive_rate)} &rarr; pipeline{" "}
                  {pct(evaluation.multi_agent_pipeline.strict_false_positive_rate)}
                </td>
              </tr>
              <tr>
                <td>Escalation rate</td>
                <td>{pct(evaluation.multi_agent_pipeline.escalation_rate)} of the test set</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
