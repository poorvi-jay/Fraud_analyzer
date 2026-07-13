# Fraud Investigation Squad

A multi-agent transaction triage system. Three specialist agents — an
anomaly-detection model (ML), a context/behavior judge (LLM), and a
policy/compliance checker (rules) — independently review a transaction. A
coordinator agent reconciles their opinions into a final verdict (`allow` /
`escalate` / `block`). See [`docs/PRD.md`](docs/PRD.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full product and
system design.

**Portfolio project, not a commercial product.** No real payment processing,
no real user financial data ever flows through the live demo. The anomaly
model can be trained on the real, publicly-available Kaggle PaySim dataset
(itself synthetic financial simulation data, not real transactions) for a
more rigorous evaluation — see [Result](#result-does-the-multi-agent-pipeline-actually-help)
below — but the live public deployment's case queue is seeded from
synthetic demo transactions regardless of which dataset the model was
trained on. See PRD §4 for the full non-goals list.

**Status: Phase 1 (MVP) complete and deployed.** Trained anomaly model, real
(mockable) context agent, policy agent, coordinator, FastAPI backend, and a
minimal case queue / case detail frontend all run end-to-end, both locally
and live:

- Frontend: https://fraud-analyzer-five.vercel.app
- Backend API: https://fraudlens-api-2zmg.onrender.com (interactive docs at `/docs`)
- Database: Supabase Postgres

Phase 2 (reviewer auth, human override, analytics dashboard) is not built
yet — see [What's not done yet](#whats-not-done-yet).

## Result: does the multi-agent pipeline actually help?

Measured on a held-out test split the anomaly model never trained on
(see [`ml/reports/baseline_comparison.md`](ml/reports/baseline_comparison.md)
for the full writeup, regenerate with `python ml/evaluate_baseline.py`),
using the **real Kaggle PaySim dataset** (6.36M transactions):

| | Anomaly model alone | Multi-agent pipeline |
|---|---|---|
| False negative rate (fraud that slips through untouched) | 2.56% | 2.56% |
| Strict false positive rate | 0.60% (flagged) | **0.43%** (wrongly auto-blocked) |
| Escalated to a human | — | 0.71% of all cases |

The honest finding here: on real PaySim, the anomaly model *alone* already
scores ROC-AUC 0.9993 on the full 1.58M-row test set — it catches nearly
all fraud on its own, because real PaySim's fraud is always the exact same
statistical signature (a TRANSFER that fully drains the origin balance).
There's no "subtle, blends into normal behavior" fraud subtype in the real
ground truth. So the multi-agent pipeline doesn't move the false-negative
needle much here — what it *does* do is cut the **wrongful-block rate**
(0.60% → 0.43%) by routing ambiguous cases to a human instead of the
anomaly model's blunter binary flag. (An earlier run against a synthetic
stand-in dataset, deliberately engineered with a stealthy fraud subtype the
anomaly model can't see, showed a much larger false-negative improvement —
9.09% → 3.03% — which is the scenario the coordinator's design was built
around; see git history. Real PaySim turned out to be an easier case for
the anomaly model alone than that synthetic scenario assumed.)

The anomaly model only sees transaction-intrinsic statistics (amount,
balance movement, type) — deliberately no per-user profile (see
`backend/app/feature_engineering.py`). The context agent is what would catch
a fraud that's modest in size and blends into normal balance movement, or
avoid over-flagging a large-but-legitimate purchase — real PaySim's fraud
pattern just doesn't happen to need that this time.

## How the coordinator decides

`docs/ARCHITECTURE.md` specifies two of the five rules explicitly (hard
policy violation → block; high anomaly + plausible context → escalate) and
defers the rest to an external diagram not included in this repo. The
remaining branches (in `backend/app/agents/coordinator_agent.py`) are filled
in around one principle: **escalation is what happens when the anomaly and
context agents disagree.** Agreement drives a confident automatic decision;
disagreement is what a human should look at.

| policy | anomaly | context | verdict |
|---|---|---|---|
| flag | any | any | `block` |
| clear | high | implausible | `block` |
| clear | high | plausible | `escalate` |
| clear | low | implausible | `escalate` |
| clear | low | plausible | `allow` |

## Running it locally

Requires Python 3.11+ and Node 20+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Data + model (already generated and committed, but here's how)

```bash
python ml/prepare_paysim.py       # writes data/transactions.csv, data/user_profiles.csv,
                                   # and ml/models/policy_calibration.json
python ml/diagnose_thresholds.py  # sanity-check policy_agent's thresholds against
                                   # whatever's currently in data/transactions.csv
python ml/train_anomaly_model.py  # trains + writes ml/models/
python ml/evaluate_baseline.py    # writes ml/reports/baseline_comparison.{md,json}
                                   # (samples 30k rows by default; --sample-size 0 for the full set)
```

If you have the real Kaggle PaySim CSV, drop it at `data/paysim.csv` before
running `prepare_paysim.py` and it's adapted automatically instead of
generating synthetic data (synthetic per-user profiles are still generated,
since PaySim itself has no meaningful user history — see
[Limitations](#limitations)). `policy_agent`'s "large reporting threshold"
rule is recalibrated to whatever dataset's actual amount distribution is
in use each time you run `prepare_paysim.py` — a fixed dollar figure
doesn't transfer between the synthetic data (median transaction ~$240) and
real PaySim (median ~$75,000).

### 2. Backend

```bash
cd backend
cp ../.env.example .env   # defaults work as-is: sqlite + mock LLM, no keys needed
pytest                    # 21 tests
uvicorn app.main:app --reload
```

Populate the case queue with some demo transactions (writes directly to the
DB, bypassing the public rate limit):

```bash
python ml/seed_demo_queue.py
```

### 3. Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev   # http://localhost:5173
```

## Configuration

See [`.env.example`](.env.example) for all backend settings. Notably:

- `LLM_PROVIDER=mock` (default) — the context agent uses a deterministic
  profile-comparison heuristic, clearly labeled `[mock heuristic]` in its
  reasoning, so the demo never overstates what's actually LLM-backed. Set
  `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` for real LLM reasoning
  (provider-agnostic by design — adding another provider is one function
  with the same signature, see `backend/app/agents/context_agent.py`).
- `DATABASE_URL` — defaults to a local SQLite file. Point it at a Supabase
  Postgres connection string (after running `supabase/schema.sql` there) to
  swap in the real database with no code changes.
- `REVIEW_RATE_LIMIT` — rate-limits the public `POST /transactions/review`
  endpoint to control LLM API cost once a real provider is wired up.

## Limitations

- **Real PaySim has (almost) no per-user history.** `ml/prepare_paysim.py`
  generates synthetic user profiles (home country, travel frequency) to
  give the context agent something to compare against, but this turned out
  to matter more than expected: ~99.9% of real PaySim's `nameOrig` values
  appear in exactly one transaction — it's a one-shot population simulation,
  not repeat customers. `typical_transaction_amount` falls back to a
  population median by transaction type for anyone with fewer than 3
  observed transactions (see `adapt_real_paysim` in `prepare_paysim.py`),
  since a "median" of one transaction is just that transaction's own
  amount. This is a documented, discovered limitation, not a hidden one —
  the context agent's per-user personalization is real for the small
  fraction of users who do have multiple transactions, and a
  population-level norm for everyone else.
- **The context agent defaults to a mock heuristic**, not a live LLM call —
  no API key is configured in this environment. The heuristic is designed
  to be a reasonable stand-in (see `_run_mock` in `context_agent.py`) and
  the Anthropic-backed path is implemented and ready, just untested against
  a live key.
- **Policy rules are illustrative**, not derived from actual regulatory
  requirements (see PRD non-goals). Thresholds are calibrated to whichever
  dataset is currently loaded (see above), not to real-world regulatory
  dollar figures.
- **Real PaySim's fraud pattern is uniformly obvious** (always a full
  balance drain via TRANSFER), which means the anomaly model alone already
  performs very well against it — see the Result section above for what
  that does and doesn't say about the multi-agent design.

## What's not done yet (Phase 2 / stretch, per the PRD)

- Reviewer auth (Supabase Auth) and the human override endpoint —
  `human_reviews` table exists in the schema and is designed for this, but
  nothing writes to it yet.
- Analytics dashboard (verdict distribution, agent agreement rate charts).
- Real LLM context agent in production — currently `LLM_PROVIDER=mock` on
  the live deployment (no API key configured yet); the Anthropic-backed
  path is implemented, just switching it on is pending a cost/quality
  decision (see PRD open questions).
- PDF export, compliance webhook stub (stretch, cut first per the PRD).

## Repo layout

```
backend/    FastAPI app, agents, persistence (SQLAlchemy), tests
ml/         data generation, threshold calibration/diagnostics, model training,
            baseline evaluation, demo seeding
frontend/   React + Vite case queue / case detail UI
supabase/   Postgres schema for a real Supabase project
docs/       PRD.md, ARCHITECTURE.md
```