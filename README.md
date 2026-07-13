# Fraud Investigation Squad

A multi-agent transaction triage system. Three specialist agents — an
anomaly-detection model (ML), a context/behavior judge (LLM), and a
policy/compliance checker (rules) — independently review a transaction. A
coordinator agent reconciles their opinions into a final verdict (`allow` /
`escalate` / `block`). See [`docs/PRD.md`](docs/PRD.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full product and
system design.

**Portfolio project, not a commercial product.** Synthetic data only — no
real payment processing, no real financial data. See PRD §4 for the full
non-goals list.

**Status: Phase 1 (MVP) complete.** Trained anomaly model, real (mockable)
context agent, policy agent, coordinator, FastAPI backend, and a minimal
case queue / case detail frontend all run end-to-end locally. Phase 2
(reviewer auth, human override, analytics dashboard) and real deployment
are not built yet — see [What's not done yet](#whats-not-done-yet).

## Result: does the multi-agent pipeline actually help?

Measured on a held-out test split the anomaly model never trained on
(see [`ml/reports/baseline_comparison.md`](ml/reports/baseline_comparison.md)
for the full writeup, regenerate with `python ml/evaluate_baseline.py`):

| | Anomaly model alone | Multi-agent pipeline |
|---|---|---|
| False negative rate (fraud that slips through untouched) | 9.09% | **3.03%** |
| False positive rate | 1.52% (flagged) | 0.14% (wrongly auto-blocked) |
| Escalated to a human | — | 2.31% of all cases |

The anomaly model only sees transaction-intrinsic statistics (amount,
balance movement, type) — deliberately no per-user profile (see
`backend/app/feature_engineering.py`). It reliably catches large,
balance-draining "obvious" fraud, but misses fraud that's modest in size and
blends into normal balance movement, and over-flags large-but-legitimate
purchases (e.g. a frequent traveller's big purchase abroad) — it has no way
to know what's normal *for that specific user*. The context agent does. The
coordinator escalates exactly the cases where the two disagree, instead of
guessing or escalating everything — which is why the false-negative rate
drops by two-thirds while only 2.3% of cases go to a human.

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
python ml/prepare_paysim.py       # writes data/transactions.csv, data/user_profiles.csv
python ml/train_anomaly_model.py  # trains + writes ml/models/
python ml/evaluate_baseline.py    # writes ml/reports/baseline_comparison.{md,json}
```

If you have the real Kaggle PaySim CSV, drop it at `data/paysim.csv` before
running `prepare_paysim.py` and it's adapted automatically instead of
generating synthetic data (synthetic per-user profiles are still generated,
since PaySim itself has no user history — see [Limitations](#limitations)).

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

- **PaySim has no real per-user history.** `ml/prepare_paysim.py` generates
  synthetic user profiles (typical amount, home country, travel frequency)
  to give the context agent something to compare against. This is a
  documented limitation, not a hidden one — it's not claimed to generalize
  to real-world fraud patterns.
- **The context agent defaults to a mock heuristic**, not a live LLM call —
  no API key is configured in this environment. The heuristic is designed
  to be a reasonable stand-in (see `_run_mock` in `context_agent.py`) and
  the Anthropic-backed path is implemented and ready, just untested against
  a live key.
- **Policy rules are illustrative**, not derived from actual regulatory
  requirements (see PRD non-goals).

## What's not done yet (Phase 2 / stretch, per the PRD)

- Reviewer auth (Supabase Auth) and the human override endpoint —
  `human_reviews` table exists in the schema and is designed for this, but
  nothing writes to it yet.
- Analytics dashboard (verdict distribution, agent agreement rate charts).
- Real deployment (Vercel / Render / a real Supabase project) — everything
  above runs locally only; `supabase/schema.sql` is ready to apply whenever
  a real project exists.
- PDF export, compliance webhook stub (stretch, cut first per the PRD).

## Repo layout

```
backend/    FastAPI app, agents, persistence (SQLAlchemy), tests
ml/         data generation, model training, baseline evaluation, demo seeding
frontend/   React + Vite case queue / case detail UI
supabase/   Postgres schema for a real Supabase project
docs/       PRD.md, ARCHITECTURE.md
```
