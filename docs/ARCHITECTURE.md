# Architecture: Fraud investigation squad

Companion doc to `PRD.md` - this covers system design, not product
scope. See the PRD for feature rationale and timeline.

## 1. System overview

```
User's browser
      |
      v
Frontend (React, Vercel)
      |
      v
Backend API (FastAPI, Render)
      |
      +----------------------+
      v                      v
Agent pipeline          Supabase
(4 agents +              (Postgres + Auth)
 coordinator)
      |
      v
LLM provider (external API call, context agent only)
```

The frontend never talks to Supabase or the LLM provider directly for
review logic - all of that is mediated by the backend, so API keys and
service-role database credentials stay server-side. The frontend does
use the Supabase client SDK directly for one thing: authentication (see
Section 5), since Supabase Auth is designed to be called from the
client and issues a JWT the backend then verifies.

## 2. Components

**Frontend (React, Vercel)**
- Case queue - paginated list, filterable by verdict
- Case detail - all agent opinions + coordinator reasoning + override
  action (if signed in)
- Analytics - verdict distribution, agent agreement rate, false
  positive trend
- Login - Supabase Auth, gates the override action only

**Backend API (FastAPI, Render/Railway)**
- Orchestrates the four agents per request
- Persists transactions, opinions, and review results to Supabase
- Verifies reviewer JWTs on the override endpoint
- Exposes analytics as pre-aggregated endpoints (aggregation happens
  in SQL, not client-side, so the frontend stays simple)

**Agent pipeline**
- `anomaly_agent` - trained ML model (XGBoost or RandomForest), scores
  0-1, includes SHAP-based reasoning
- `context_agent` - LLM call, judges plausibility against the user's
  synthetic profile
- `policy_agent` - deterministic rule checks, no ML/LLM involved
- `coordinator_agent` - reconciliation logic; see the agent-flow
  diagram from the initial scoping conversation for the full decision
  table (hard policy violation -> block; high anomaly + plausible
  context -> escalate; low anomaly + implausible context -> escalate;
  otherwise allow)

**Supabase**
- Postgres for all persisted data (schema in Section 3)
- Auth for reviewer login (email/password or magic link - either
  works for a single-reviewer demo)

**LLM provider**
- Called only by `context_agent`
- Provider-agnostic by design (Anthropic and OpenAI SDKs both fit the
  same function signature already scaffolded)

## 3. Data model

```
erDiagram
  USER_PROFILES ||--o{ TRANSACTIONS : has
  TRANSACTIONS ||--o{ AGENT_OPINIONS : produces
  TRANSACTIONS ||--|| REVIEW_RESULTS : resolves_to
  REVIEW_RESULTS ||--o{ HUMAN_REVIEWS : overridden_by

  USER_PROFILES {
    string user_id PK
    date account_created
    string home_country
    float typical_transaction_amount
    string travel_frequency
  }
  TRANSACTIONS {
    string id PK
    string user_id FK
    float amount
    string transaction_type
    float origin_balance_before
    float origin_balance_after
    string location_country
    boolean is_fraud_ground_truth
    timestamp occurred_at
  }
  AGENT_OPINIONS {
    string id PK
    string transaction_id FK
    string agent_name
    float score
    boolean flag
    string reasoning
  }
  REVIEW_RESULTS {
    string id PK
    string transaction_id FK
    string final_verdict
    string coordinator_reasoning
  }
  HUMAN_REVIEWS {
    string id PK
    string review_result_id FK
    string reviewer_id FK
    string decision
    string note
  }
```

(This renders automatically if pasted into a GitHub README or any
mermaid-aware viewer.)

`is_fraud_ground_truth` on `TRANSACTIONS` is nullable - populated for
the PaySim-derived evaluation set (used for the baseline comparison),
null for anything reviewed live through the demo, since live demo
transactions have no ground truth.

## 4. API endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/health` | Liveness check | None |
| POST | `/transactions/review` | Run the full pipeline on a transaction, persist the result | None (public demo) |
| GET | `/transactions` | List the case queue, filterable by verdict | None |
| GET | `/transactions/{id}` | Case detail - all agent opinions + verdict | None |
| POST | `/reviews/{id}/override` | Human reviewer decision on an escalated case | Reviewer JWT required |
| GET | `/analytics/verdict-distribution` | Counts by verdict | None |
| GET | `/analytics/agent-agreement-rate` | How often agents agree vs. conflict | None |
| GET | `/analytics/evaluation-summary` | Baseline vs. multi-agent comparison stats | None |

Queue/detail/analytics endpoints stay unauthenticated deliberately -
this is a demo meant to be browsed by recruiters without a login. Only
the action that writes a human judgment (`override`) is gated.

## 5. Auth flow

1. Reviewer signs in via the Supabase Auth client SDK directly from
   the React frontend (email/password or magic link).
2. Supabase returns a JWT to the frontend.
3. The frontend sends that JWT in the `Authorization` header on the
   override request only.
4. FastAPI verifies the JWT against Supabase's public key before
   writing to `human_reviews`.

No custom auth logic needed - this is entirely Supabase Auth's default
flow, chosen specifically because it avoids building session
management from scratch.

## 6. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React, deployed on Vercel | Matches existing skillset; static hosting is effectively free at this scale |
| Backend | FastAPI, deployed on Render or Railway | Matches the FastAPI skill already listed on the resume but previously unused in a project |
| Database + Auth | Supabase (Postgres + Auth) | Same reasoning - closes the Supabase skill gap; built-in auth avoids rolling a custom login system |
| ML | scikit-learn / XGBoost + SHAP | Anomaly agent + explainability, consistent with the Grad-CAM interpretability approach used on the chest X-ray project |
| LLM | Anthropic or OpenAI SDK | Context agent reasoning |
| Analytics viz | Recharts (or Chart.js) inside the React app | Closes the PowerBI/Tableau gap without needing a separate BI project |

## 7. Security & deployment considerations

- Synthetic data only - the public demo never accepts or stores real
  financial data. A visible "demo mode" notice states this on the
  frontend.
- API keys (LLM provider, Supabase service role) live server-side only,
  in environment variables - never shipped to the frontend bundle.
- Public review endpoint is rate-limited to control LLM API cost from
  unrate-limited public traffic.
- Only the override endpoint requires auth; this keeps the demo
  browsable while still gating the one action that writes an
  attributable human decision.

## 8. Evaluation methodology

1. Hold out a test split of the PaySim-derived dataset (with ground
   truth `is_fraud` labels) that the anomaly model never trains on.
2. Run that test split through the anomaly model alone - this is the
   baseline.
3. Run the same test split through the full four-agent pipeline.
4. Compare false positive rate and false negative rate between the two.
5. Report the result in the project README and reference it directly
   on the resume - this is the one number that turns "built a fraud
   agent" into a measured claim.
