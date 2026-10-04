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

**Status: Phase 1 (MVP) and Phase 2 (reviewer auth, human override, analytics
dashboard) both deployed and live**, plus the post-PRD Razorpay ingestion
path (PRD §7.5). Trained anomaly model, real (mockable) context agent, policy
agent, coordinator, FastAPI backend, and a case queue / case detail /
analytics / reviewer sign-in frontend all run end-to-end, both locally and
live:

- Frontend: https://fraud-analyzer-five.vercel.app
- Backend API: https://fraudlens-api-2zmg.onrender.com (interactive docs at `/docs`)
- Database: Supabase Postgres

> **If the demo appears dead, give it a minute, then read this.** Both the
> API (Render free tier) and the database (Supabase free tier) sleep when
> idle. A first request after idle takes ~30s while Render wakes — that's
> normal. What is *not* normal is no response at all: the backend opens its
> database connection at import time, so if Supabase has been paused for
> inactivity, uvicorn exits and every route including `/health` hangs with
> no reply. Unpause the project in the Supabase dashboard and Render's
> restart loop recovers on its own within ~5 minutes; no redeploy needed,
> and seeded data survives the pause. This is a free-tier tradeoff, not a
> bug in the app.

See [Phase 2 setup](#phase-2-reviewer-override--analytics) for how reviewer
auth is wired up if you're standing up your own deployment.

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

These numbers are measured with `policy_agent`'s reporting threshold
calibrated on the **training rows only**. An earlier version took that
percentile over the whole dataset, test rows included — one scalar, but a
real leak, since a policy flag hard-overrides the coordinator into `block`
and the false-positive rate above is partly a policy-rule rate. Fixing it
moved the threshold $2,437,745.64 → $2,439,919.22 (+0.089%) and changed
**none** of the numbers below: zero rows in the evaluated sample fall
between the two values (9 in the full 1.59M-row test split). The leak was
real but inert — worth stating precisely rather than either hiding or
overselling.

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

## Razorpay ingestion

`POST /transactions/razorpay/simulate` accepts a Razorpay test-mode Payment
object and scores it through the same four agents, via
[`backend/app/adapters/razorpay_adapter.py`](backend/app/adapters/razorpay_adapter.py).
No agent was modified — all provider-specific mapping lives in the adapter.

**Read [`docs/RAZORPAY_ADAPTER.md`](docs/RAZORPAY_ADAPTER.md) before trusting
a verdict from this path.** The short version of what does not survive the
mapping:

| Feature | Status on Razorpay input |
|---|---|
| `amount`, `occurred_at` | exact |
| `amount_to_typical_ratio`, `is_foreign`, `account_age_days` | intact, but see below |
| `transaction_type` one-hots (5 features) | **constant → zero signal** |
| `origin_balance_before/after`, `balance_drained_ratio` (3 features) | **unavailable** |

Razorpay's Payment object carries no account balance and no country, and its
`method` field is a payment-*instrument* taxonomy where PaySim's `type` is a
fund-*flow* taxonomy — they are different axes, so there is no honest
per-method mapping. The adapter refuses to invent any of it:

- **Balances**: supply `balance_context` from your own ledger, or send
  `allow_degraded: true` and the anomaly model **is not run at all** (the
  response sets `anomaly_scored: false` and the opinion reads
  `[NOT SCORED -- inputs unavailable]`). Send neither and you get a 422.
  Defaulting balances to `0` puts the model off-manifold; defaulting them to
  `amount` reproduces PaySim's exact fraud signature and would make every
  payment look fraudulent. Both are worse than admitting the gap.
- **Country**: `international` is mapped to an `XX` sentinel meaning "not the
  customer's home country", never a specific country.
- **History**: Razorpay serves no per-customer spend history, so
  `typical_transaction_amount` has to be maintained by us, keyed by
  `customer_id` — which is what `user_profiles` already is. Cold start means
  no profile-relative signal at all.

Every response carries `feature_availability` and `adapter_warnings`; a
verdict from this endpoint should never be read without them.

Full-fidelity example (balances supplied from your own ledger, in rupees —
Razorpay's own `amount` stays in paise):

```bash
curl -X POST http://localhost:8000/transactions/razorpay/simulate -H 'Content-Type: application/json' -d '{"payment":{"id":"pay_29QQoUBi66xm2f","entity":"payment","amount":100000,"currency":"INR","status":"captured","method":"card","international":false,"customer_id":"cust_DitrYCFtCIokBO","email":"gaurav.kumar@example.com","contact":"9000090000","created_at":1718000000},"balance_context":{"origin_balance_before":5000.0,"origin_balance_after":4000.0},"profile":{"home_country":"IN","typical_transaction_amount":900.0,"travel_frequency":"rare","account_age_days":400}}'
```

Drop `balance_context` and you get a 422 explaining why. Add
`"allow_degraded": true` instead and it scores with the anomaly model
skipped, `anomaly_scored: false`, and the reason in the warnings.

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
pytest
uvicorn app.main:app --reload
```

Reviewer sign-in and the override endpoint additionally need
`SUPABASE_URL`/`SUPABASE_ANON_KEY` in `.env` (see
[Phase 2 setup](#phase-2-reviewer-override--analytics)) — everything else
(case queue, case detail, analytics) works without them.

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

- `LLM_PROVIDER` — `mock` (default), `openai`, or `anthropic`. The mock is a
  deterministic profile-comparison heuristic, clearly labeled
  `[mock heuristic]` in its reasoning, so the demo never overstates what's
  actually LLM-backed. `openai` (GPT-5.6 Luna) needs `OPENAI_API_KEY`;
  `anthropic` needs `ANTHROPIC_API_KEY`. Provider-agnostic by design —
  adding another is one function with the same signature, see
  `backend/app/agents/context_agent.py`. **Keys are server-side only**: the
  browser only ever receives `VITE_*` vars, and no route returns a key.
- `LLM_MONTHLY_BUDGET_USD` / `LLM_DAILY_BUDGET_USD` — see
  [Cost controls](#cost-controls) below. These are not optional decoration;
  they are what bounds the bill.
- `DATABASE_URL` — defaults to a local SQLite file. Point it at a Supabase
  Postgres connection string (after running `supabase/schema.sql` there) to
  swap in the real database with no code changes.
- `REVIEW_RATE_LIMIT` — rate-limits the public scoring endpoints. Note this
  is **per IP** (slowapi's default key), so it shapes bursts but is not a
  global ceiling; the budget caps below are what actually bound spend.

## Cost controls

The context agent is the only component that calls an LLM: **one call per
transaction scored**, ~315 tokens (~260 in, ~55 out), about $0.00012 at
GPT-5.6 Luna's $0.20/$1.20 per 1M. Reading the queue, a case, or the
analytics costs nothing — agent reasoning is persisted when the verdict is
produced, not regenerated on view.

Three guards, because a shared key makes an unbounded demo everyone else's
problem too:

1. **Persistent spend caps.** `llm_daily_spend` (one row per UTC day) backs
   a daily and a monthly cap. It lives in the database on purpose: an
   in-memory counter resets on every Render cold start, so it could log
   spend but never cap it. Checks happen before each call, count the call
   about to be made, and **fail closed** — an unreadable ledger means no
   call. When a cap is hit the agent serves the mock, labeled
   `[LLM budget reached: ...]`, so the demo degrades visibly instead of
   dying. Defaults assume a $5/month key shared across four projects:
   $1.00/month on Render, $0.25 locally (separate ledgers — local dev uses
   SQLite and cannot see the deployed one).
2. **`ml/evaluate_baseline.py` forces the mock** whatever `LLM_PROVIDER`
   says. One row is one call and the default sample is 30,000, so the
   documented command would otherwise cost ~$3.54, and `--sample-size 0`
   against real PaySim ~$188. Live evaluation needs `--allow-llm`, is
   capped by `--max-live-calls` (default 200), is checked against the
   remaining budget, and asks for confirmation. There is also a
   methodological reason: a live LLM makes the reported numbers
   non-reproducible, so the report records which provider produced it and
   says so explicitly if a run ended up mixed.
3. **Misconfiguration fails loudly.** A missing or invalid key, an unknown
   provider, or a model with no pricing entry raises rather than falling
   back — returned as HTTP 503 naming the problem. Only *transient*
   failures (timeout, 429, 5xx, unparseable output) fall back to the mock,
   labeled inline. A silent fallback would mean serving mock verdicts while
   believing the demo was LLM-backed.

Set an account-level spend limit in your provider's console as well. That
limit is org-wide, so it protects the wallet but not the other projects
sharing the key — which is what the per-project caps above are for.

## Phase 2: reviewer override & analytics

Per PRD §7.2: a reviewer can sign in and override an escalated case with a
note, and an analytics view surfaces verdict distribution, agent agreement
rate, and verdict volume over time. The case queue, case detail, and
analytics dashboard all stay publicly viewable without signing in — only
`POST /reviews/{id}/override` requires a reviewer session.

**How auth works**: the frontend signs a reviewer in directly against
Supabase Auth (`frontend/src/auth.tsx`) and sends the resulting access token
as a bearer header on override requests. The backend verifies it by calling
Supabase's Auth API (`auth.get_user`, see `backend/app/auth.py`) rather than
decoding the JWT locally — no shared secret to keep in sync, and it works
regardless of which signing algorithm the Supabase project uses.

**One-time setup** (already done on the live deployment above; needed again
only if you're standing up your own — not automated, needs your own
Supabase/Render/Vercel dashboard access):

1. Run the `alter table` migration block in [`supabase/schema.sql`](supabase/schema.sql)
   against your Supabase project (adds `human_reviews.reviewed_at` and a
   decision check constraint to the table that already existed from Phase 1).
2. Create one reviewer account by hand under Supabase Auth → Users
   (email/password — this is a single-demo-account setup, not self-serve
   signup, per PRD's "not multi-tenant" non-goal).
3. Set `SUPABASE_URL` / `SUPABASE_ANON_KEY` in the backend env (Render) and
   `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` in the frontend env
   (Vercel) — same project, same anon key on both sides.
4. Redeploy both.

**Locally**: same two env vars in `backend/.env` and `frontend/.env` (see
`.env.example` in each), pointed at the same Supabase project (or a free
Supabase project of your own — no need to share the live one).

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
- **The context agent defaults to a mock heuristic**, not a live LLM call.
  The heuristic is designed to be a reasonable stand-in (see `_run_mock` in
  `context_agent.py`). The OpenAI path (GPT-5.6 Luna) has been verified
  end-to-end against a live key with `scripts/smoke_llm.py` — call shape,
  response parsing, token accounting and the spend ledger all confirmed on
  one real call. The **Anthropic path remains implemented but unexercised**.
  Every verdict in the committed reports and in the seeded demo queue still
  comes from the mock, and the deployed backend still runs it.
- **Policy rules are illustrative**, not derived from actual regulatory
  requirements (see PRD non-goals). Thresholds are calibrated to whichever
  dataset is currently loaded (see above), not to real-world regulatory
  dollar figures.
- **Real PaySim's fraud pattern is uniformly obvious** (always a full
  balance drain via TRANSFER), which means the anomaly model alone already
  performs very well against it — see the Result section above for what
  that does and doesn't say about the multi-agent design.
- **Razorpay-sourced transactions are scored on strictly less information
  than PaySim-sourced ones.** 3 of the anomaly model's 9 features have no
  Razorpay equivalent and 5 more go constant; in degraded mode the model is
  skipped entirely and the verdict rests on the context and policy agents
  alone. Because a skipped anomaly opinion carries `flag=False`, the
  coordinator table reduces to *context implausible → escalate, context
  plausible → allow* — so a degraded Razorpay payment **can be auto-allowed
  without the anomaly model ever having run**. It can never be auto-blocked
  on a fabricated signal, which is the conservative direction, but "allow"
  from this path is a weaker claim than "allow" from the PaySim path. See
  [`docs/RAZORPAY_ADAPTER.md`](docs/RAZORPAY_ADAPTER.md).
- **The anomaly model has never been evaluated on Razorpay-shaped data.**
  Every number in the Result section is measured on PaySim. The adapter is
  tested for correct mapping and correct degradation, not for predictive
  accuracy on real Razorpay traffic — no labelled Razorpay fraud data was
  used at any point, so there is no honest accuracy claim to make for that
  path yet.
- **`typical_transaction_amount` is not a Razorpay-derived quantity.** For
  Razorpay input it comes from whatever history we have accumulated
  ourselves. On a cold start there is none, and the context agent's
  profile-relative judgement is only as good as that store.

## What's not done yet

**One committed MVP item is still unmet, and it is not a stretch item.**
PRD §7.1 lists "real context agent — replace placeholder with an actual LLM
call" as must-ship for Phase 1.

The provider question (PRD §11) is now settled: **GPT-5.6 Luna via the
OpenAI API**, chosen on cost — about 4.5x cheaper per call than the
Haiku 4.5 alternative, which matters on a key shared across four projects.
The client, token accounting, spend caps and failure handling are all
written and unit-tested.

The path is verified locally: `scripts/smoke_llm.py` scores one real
transaction against a live key and checks the call shape, the parsed
response, billed tokens against the estimate, and the ledger write. It
passes.

What is **not** done: the deployed backend still runs `LLM_PROVIDER=mock`,
so the acceptance criterion is met locally and unmet in production. Nothing
overstates itself in the meantime — the mock is labelled `[mock heuristic]`
wherever its reasoning appears — but the live demo a visitor clicks is
still heuristic-backed, and this line stays until that changes.

Also outstanding:

- **The Razorpay adapter is doc-verified, not API-verified** — see
  [`scripts/verify_razorpay_schema.py`](#scriptsverify_razorpay_schemapy)
  above, which exists to close this and has not been run.
- **Analytics reports verdict volume over time, not false-positive rate over
  time**, which PRD §7.2 originally specified. This is a deliberate
  narrowing — the live queue has no ground-truth labels, so the rate is not
  computable there; see the scope correction in PRD §7.2.

Genuinely cut, per PRD §7.3: PDF export, compliance webhook stub, trend
charts beyond the above.

## Repo layout

```
backend/    FastAPI app, agents, persistence (SQLAlchemy), tests
ml/         data generation, threshold calibration/diagnostics, model training,
            baseline evaluation, demo seeding
frontend/   React + Vite case queue / case detail / analytics / reviewer sign-in UI
scripts/    one-off verification tooling (not imported by the app)
supabase/   Postgres schema for a real Supabase project
docs/       PRD.md, ARCHITECTURE.md, RAZORPAY_ADAPTER.md
render.yaml Blueprint documenting the backend's Render deployment
```

### `scripts/verify_razorpay_schema.py`

The Razorpay adapter's field mapping was written from Razorpay's published
Payment entity schema. This script checks those assumptions against real
test-mode API responses instead, and reports field by field where docs and
reality diverge. One authenticated GET, no writes.

```bash
export RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
export RAZORPAY_KEY_SECRET=xxxxxxxx
python scripts/verify_razorpay_schema.py
```

Live (`rzp_live_`) keys are refused with no override flag — a live key would
pull real customers' contact details into a local file. The report records
only whether identity fields are *present*, never their values.

**This has not been run yet**, so the adapter remains doc-verified, not
API-verified. See [Limitations](#limitations).