# PRD: Fraud investigation squad

**Status:** Draft v1
**Owner:** Poorvi J Tatti
**Type:** Portfolio project (not a commercial product - see Non-goals)

## 1. Summary

A multi-agent transaction triage tool. Three specialist agents - anomaly
detection (ML), context/behavior (LLM), and policy/compliance (rules) -
independently review a transaction. A coordinator agent reconciles their
opinions into a final decision (allow / escalate / block). Escalated
cases go to a human reviewer, who can override the decision. An
analytics view surfaces patterns across all reviewed transactions.

## 2. Problem statement

Single-model fraud classifiers optimize for one thing: statistical
anomaly. They miss cases where a transaction looks unremarkable but
doesn't fit a specific user's behavior, and they over-flag cases that
are anomalous but explainable (e.g. a large purchase from a frequent
traveller). This project tests whether combining independent,
differently-specialized agents - and building in a genuine mechanism for
them to disagree - catches more of what a single model misses, without
just escalating everything to a human.

## 3. Goals

- Demonstrate a real multi-agent architecture where agents can
  genuinely disagree and a coordinator resolves the conflict, not just
  three prompts chained in sequence.
- Produce a measurable result: multi-agent pipeline vs. single-model
  baseline on the same held-out test set (false positive / false
  negative rate).
- Ship something a recruiter or interviewer can actually click through -
  a live case queue, not just a notebook.
- Close three specific skill gaps on the current resume: FastAPI,
  Supabase, and data visualization (PowerBI/Tableau are listed but
  unused elsewhere).

## 4. Non-goals

- **Not a commercial product.** No real payment processing, no real
  user financial data, no monetization. See the discussion in-thread:
  fraud tooling carries real compliance and liability obligations
  (data protection law, PCI-DSS if it touched real payment data) that
  are appropriate for a licensed, insured company - not a student
  portfolio project.
- Not a production-grade compliance system. Policy rules are
  illustrative, not derived from actual regulatory requirements.
- Not multi-tenant. One demo environment, not customer-isolated data.

## 5. Target user / persona

Primary: a fraud operations analyst at a fintech, reviewing transactions
the automated system has escalated. Secondary (the actual audience for
this project): recruiters and interviewers evaluating the build.

## 6. User stories

1. As an analyst, I want to see a queue of reviewed transactions with
   their verdicts, so I can prioritize what needs my attention.
2. As an analyst, I want to open a transaction and see *why* each
   agent reached its opinion, not just a single score, so I can trust
   or challenge the system's reasoning.
3. As an analyst, I want to override an escalated decision with a note,
   so my judgment is captured and the system has a record of outcomes.
4. As a team lead, I want to see aggregate patterns (verdict mix,
   how often agents disagree, false positive rate over time), so I can
   tell whether the system is calibrated well.
5. As a visitor evaluating this project, I want to interact with a live
   demo on synthetic data without needing to run anything locally.

## 7. Features

### 7.1 MVP (Phase 1) - must ship

| Feature | Description | Acceptance criteria |
|---|---|---|
| Agent pipeline | Anomaly, context, policy agents + coordinator | Given a transaction + profile, returns a verdict with per-agent reasoning. Already scaffolded and smoke-tested. |
| Trained anomaly model | Replace placeholder heuristic with a real model trained on PaySim | ROC-AUC and confusion matrix reported on a held-out test set |
| Real context agent | Replace placeholder with an actual LLM call | Given the same test cases used during scaffolding, returns structured plausibility judgments |
| Case queue | List view of reviewed transactions | Shows verdict, amount, timestamp; filterable by verdict |
| Case detail | "Case file" view of one transaction | Shows all three agent opinions + coordinator reasoning side by side |
| Live deployment | Publicly accessible URL | Runs on synthetic PaySim-derived data only, with a visible "demo mode" notice |
| Baseline comparison | Multi-agent pipeline vs. anomaly-model-only | Reports false positive / false negative rate for both, on the same test set |

### 7.2 Phase 2 - committed scope (per user decision: "MVP + both")

| Feature | Description | Acceptance criteria |
|---|---|---|
| Human review override | Reviewer approves/rejects an escalated case with a note | Decision + note persisted, visible on the case detail view, linked to the original review |
| Reviewer auth | Login gate for the override action only | Queue and analytics remain publicly viewable (demo purpose); override requires a signed-in reviewer via Supabase Auth |
| Analytics dashboard | Aggregate charts over all reviewed transactions | Verdict distribution, agent agreement/disagreement rate, ~~false positive trend over time~~ → verdict volume over time (see scope correction below) |

**Scope correction — "false positive trend over time" was not buildable as
specified.** A false positive rate requires ground truth: you can only call a
`block` wrong if you know the transaction was legitimate. The live case queue
has no labels. It is seeded from PaySim rows, but what is persisted is the
pipeline's verdict, not the source row's `isFraud` — and even if it were, a
"false positive rate over time" computed over replayed historical rows measures
the dataset's ordering, not the system's calibration drifting.

So `/analytics/verdict-trend` reports **verdict volume over time** instead, and
the honest false-positive number lives where it can be computed properly:
`/analytics/evaluation-summary`, served from
`ml/reports/baseline_comparison.json`, measured against the held-out labelled
test split. That is the number quoted in the README.

This is a deliberate narrowing, not an oversight. The alternative — plotting a
"false positive rate" the data cannot support — would have been the single most
misleading chart in the project. User story 4's "false positive rate over time"
is unmet, and stays unmet, for that reason.

### 7.3 Stretch - cut first if time is short

- Exportable PDF report for a single case
- Stubbed "notify compliance team" webhook
- Historical fraud-rate trend charts beyond what's in 7.2

### 7.4 Explicitly out of scope

- Real payment processing or live transaction data
- Multi-tenant auth / customer data isolation
- A real model-retraining feedback loop from human overrides (worth
  *designing for* - the human_reviews table is structured so this is
  possible later - but not implementing it)
- Any form of monetization

### 7.5 Added after this PRD was written: Razorpay ingestion

`POST /transactions/razorpay/simulate` and the `/razorpay` frontend screen
were built after §7 was frozen and were never scoped here. Recording it so
this document does not read as the complete feature set:

- The adapter maps a Razorpay test-mode Payment object onto the same four
  agents, with no agent modified.
- It is verified against Razorpay's *published schema*, not against live API
  responses. `scripts/verify_razorpay_schema.py` exists to close that gap and
  has not been run.
- No accuracy claim is made for this path. Every number in §8 and in the
  README is measured on PaySim; no labelled Razorpay fraud data was used at
  any point.

See `docs/RAZORPAY_ADAPTER.md` for what does and does not survive the mapping.

## 8. Success metrics

- **Technical:** measurable improvement (or at minimum, a documented,
  explained case) where the multi-agent pipeline's decision differs
  from - and outperforms - the anomaly-model-only baseline.
- **Portfolio:** a live, clickable deployment plus a GitHub repo with
  this PRD and the architecture doc included, usable directly in
  placement interviews.
- **Personal:** working, demonstrable proof of the four resume skills
  this project was scoped to close (Agent orchestration, FastAPI,
  Supabase, data visualization).

## 9. Timeline & milestones

Assumes ~4 weeks at 5-8 hrs/week. This scope (MVP + both Phase 2
items) is ambitious for that window - the sequencing below assumes
stretch items (7.3) are cut entirely, and treats them as explicit
future work if time allows.

| Week | Focus |
|---|---|
| 1 | Anomaly model training + evaluation; policy agent finalized; FastAPI endpoint returns real (non-heuristic) anomaly scores |
| 2 | Context agent wired to a real LLM; Supabase schema created; transactions/reviews persisted; baseline comparison run and documented |
| 3 | React frontend: case queue + case detail; deployment (Vercel + Render + Supabase); Supabase Auth wired for reviewer login |
| 4 | Human override flow; analytics dashboard; polish, demo-mode safeguards, README + write-up; buffer for deployment debugging |

If running full-time instead of part-time, compress to roughly 10-14
days on the same sequencing.

## 10. Risks & assumptions

- **Scope risk:** Phase 2 (auth + override + analytics) roughly doubles
  the surface area versus MVP alone. If week 3-4 slips, cut the
  analytics dashboard before cutting the override flow - override
  completes the core "agents escalate, human decides" narrative;
  analytics is additive polish.
- **Data risk:** PaySim has no real per-user history, so context-agent
  "plausibility" reasoning depends on synthetic profiles generated in
  `prepare_paysim.py`. This is a documented limitation, not a hidden one
  - worth stating explicitly in the project README so it reads as
  intentional scoping, not an oversight.
- **Cost risk:** live LLM calls on a public demo can run up API costs if
  unrate-limited. Mitigation: cache responses for the demo transaction
  set, or rate-limit the public review endpoint.
- **Assumption:** the "PaySim + synthetic profiles" approach is
  sufficient for demo and evaluation purposes; this is not claimed to
  generalize to real-world fraud patterns.

## 11. Open questions

- ~~Which LLM provider to standardize on for the context agent (cost vs.
  quality tradeoff not yet evaluated).~~ **Resolved: OpenAI GPT-5.6 Luna.**

  Decided on cost, because the quality side was never actually evaluated
  and this document should not pretend otherwise. Measured from the real
  prompt `context_agent` builds (~260 input, ~55 output tokens), one scored
  transaction costs ~$0.00012 on Luna ($0.20/$1.20 per 1M) against
  ~$0.00054 on Claude Haiku 4.5 ($1.00/$5.00) — roughly 4.5x. In absolute
  terms that is ~40 cents a month at realistic demo volume, which would not
  decide anything on its own; it matters because the key is shared across
  four projects with a $5/month ceiling between them.

  Two consequences worth recording. First, the abuse ceiling scales with
  the same factor, and that is where the difference is real money. Second,
  because provider choice here is a config value rather than a code change,
  the "provider-agnostic" claim in §5 is now demonstrated rather than
  asserted — both clients exist behind one signature.

  Per-project spend caps were added alongside this (see the README's cost
  controls section). The provider decision and the guardrails around it
  arrived together on purpose: a shared key makes an uncapped demo
  everyone else's problem.
- Whether the reviewer login is a single shared demo account or
  supports creating new reviewer accounts (affects Supabase Auth setup
  complexity slightly).
