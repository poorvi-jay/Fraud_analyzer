import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getEvaluationSummary } from "../api";
import type { EvaluationSummary } from "../types";

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

const AGENTS = [
  {
    name: "Anomaly agent",
    tag: "ML",
    body:
      "A trained XGBoost model scoring purely on transaction-intrinsic statistics -- amount, balance movement, type. Deliberately has no idea who the user is, so it flags what's statistically unusual regardless of context.",
  },
  {
    name: "Context agent",
    tag: "LLM",
    body:
      "Judges whether a transaction fits this specific user's own behavior -- typical spend, home country, travel history. Catches the reverse case: a transaction that's off-profile even though it's not statistically extreme.",
  },
  {
    name: "Policy agent",
    tag: "Rules",
    body:
      "Deterministic, illustrative compliance checks -- mule-pattern balance drains, large-amount reporting, new-account abuse. No ML or LLM involved; a hard violation here always wins.",
  },
  {
    name: "Coordinator",
    tag: "Logic",
    body:
      "Reconciles the three opinions with a plain decision table, not another model call. Its whole thesis: escalation to a human is exactly what happens when the anomaly and context agents disagree with each other.",
  },
];

export default function Home() {
  const [evaluation, setEvaluation] = useState<EvaluationSummary | null>(null);

  useEffect(() => {
    getEvaluationSummary()
      .then(setEvaluation)
      .catch(() => setEvaluation(null));
  }, []);

  return (
    <div className="home">
      <section className="hero">
        <h2>A second opinion, not a single verdict</h2>
        <p>
          Most fraud classifiers score a transaction once and act on that score. This project tests a different
          idea: run three independently-specialized agents -- an anomaly model, a per-user context judge, and a
          rules-based policy checker -- and let a coordinator reconcile what they say. When two of them genuinely
          disagree, the case goes to a human instead of getting auto-decided. Agreement is what earns an automatic
          allow or block.
        </p>
      </section>

      <section>
        <h3>How the four agents work</h3>
        <div className="opinions">
          {AGENTS.map((agent) => (
            <div key={agent.name} className="opinion-card">
              <h4>{agent.name}</h4>
              <p className="score">{agent.tag}</p>
              <p>{agent.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>Measured result</h3>
        {evaluation ? (
          <>
            <div className="stat-row">
              <div className="stat-card">
                <div className="label">Test set</div>
                <div className="value">
                  {evaluation.n_test.toLocaleString()} <span className="subtle">txns</span>
                </div>
              </div>
              <div className="stat-card">
                <div className="label">False negative rate</div>
                <div className="value">
                  {pct(evaluation.multi_agent_pipeline.strict_false_negative_rate)}{" "}
                  <span className="subtle">vs {pct(evaluation.baseline_anomaly_only.false_negative_rate)} alone</span>
                </div>
              </div>
              <div className="stat-card">
                <div className="label">Wrongful auto-block rate</div>
                <div className="value">
                  {pct(evaluation.multi_agent_pipeline.strict_false_positive_rate)}{" "}
                  <span className="subtle">vs {pct(evaluation.baseline_anomaly_only.false_positive_rate)} alone</span>
                </div>
              </div>
              <div className="stat-card">
                <div className="label">Escalated to a human</div>
                <div className="value flagged">{pct(evaluation.multi_agent_pipeline.escalation_rate)}</div>
              </div>
            </div>
            <p className="subtle">
              Measured on a held-out test split the anomaly model never trained on. Full breakdown on the{" "}
              <Link to="/analytics">analytics page</Link>.
            </p>
          </>
        ) : (
          <p className="subtle">
            No evaluation report generated yet in this environment -- run <code>python ml/evaluate_baseline.py</code>{" "}
            against the backend to produce one.
          </p>
        )}
      </section>

      <section>
        <h3>Explore this demo</h3>
        <div className="home-actions">
          <Link to="/queue" className="home-action">
            <strong>Case queue &rarr;</strong>
            <span>Browse reviewed transactions. Click into any case to see all three agent opinions side by side.</span>
          </Link>
          <Link to="/analytics" className="home-action">
            <strong>Analytics &rarr;</strong>
            <span>Verdict distribution, agent agreement rate, and the baseline-vs-pipeline comparison.</span>
          </Link>
          <Link to="/login" className="home-action">
            <strong>Reviewer sign-in &rarr;</strong>
            <span>Sign in to approve or reject an escalated case yourself -- the human-in-the-loop half of the thesis.</span>
          </Link>
        </div>
      </section>

      <section>
        <h3>About this project</h3>
        <p>
          Portfolio project, not a commercial product -- no real payment processing or real user financial data ever
          flows through this demo; every transaction here is synthetic or derived from the public Kaggle PaySim
          dataset. Full write-up, architecture doc, and source on{" "}
          <a href="https://github.com/poorvi-jay/Fraud_analyzer" target="_blank" rel="noreferrer">
            GitHub
          </a>
          .
        </p>
      </section>
    </div>
  );
}
