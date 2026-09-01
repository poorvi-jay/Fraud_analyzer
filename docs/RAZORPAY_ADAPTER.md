# Razorpay ingestion: what maps, what degrades, what breaks

`backend/app/adapters/razorpay_adapter.py` accepts a Razorpay test-mode
Payment object and produces the internal transaction dict the four agents
already consume. **No agent was modified.** Everything provider-specific is
resolved in the adapter.

Endpoint: `POST /transactions/razorpay/simulate`. Like `/transactions/simulate`,
it never writes to the database.

## Why a separate endpoint rather than extending `/simulate`

The Razorpay response has to carry `feature_availability` and
`adapter_warnings` — a verdict from this path is not safe to read without
them. Folding those into `SimulateResponse` would hang permanently-null
fields on every playground call, and a discriminated-union request body
makes `/docs` noticeably worse for someone trying the API cold. The cost of
a separate route is a little duplicated wiring; the benefit is that the
existing PaySim contract is byte-for-byte unchanged and the frontend needed
no edit. The four-agent orchestration itself is *not* duplicated — both
paths call `pipeline.run_agents()`.

## The mapping

| Internal field | Razorpay source | Fidelity |
|---|---|---|
| `amount` | `amount` ÷ 10^minor-unit-exponent | **Exact.** Razorpay quotes minor units (paise). Zero-decimal (JPY, KRW…) and three-decimal (KWD, BHD…) currencies are handled; anything unlisted assumes 2 and emits a warning. |
| `occurred_at` | `created_at` (UNIX seconds, UTC) | **Exact.** |
| `user_id` | `customer_id`, else SHA-256 of `email`/`contact` | **Good**, with a caveat below. |
| `transaction_type` | always `PAYMENT` | **Degraded — signal destroyed.** |
| `location_country` | derived from `international` | **Degraded — boolean only.** |
| `origin_balance_before/after` | *nothing* | **Unavailable.** |
| `balance_drained_ratio` | *nothing* | **Unavailable.** |
| `amount_to_typical_ratio` | our own `user_profiles` row | **Requires us to maintain history.** |

## The four honest gaps

### 1. Balances — no equivalent exists (3 of the model's 9 features)

Razorpay's Payment object has `amount`, `fee`, `tax`, `amount_refunded`.
None is a balance. The two obvious defaults are both actively harmful:

- **Default to 0** → `origin_balance_before = 0` with a nonzero amount, and
  `balance_drained_ratio = 0`. That combination appears nowhere in training;
  the model extrapolates off-manifold and its confidence means nothing.
- **Default to `amount`** → `balance_drained_ratio = 1.0`, which is
  *precisely* real PaySim's fraud signature (a transfer that fully drains
  the origin balance). Every Razorpay payment would look like textbook fraud.

So the adapter leaves them `None` and the caller picks:

- **Supply `balance_context`** from your own ledger (major units — rupees,
  not paise) → full-fidelity scoring, all four agents run normally.
- **Send `allow_degraded: true`** → the anomaly model **is not run at all**.
  The response sets `anomaly_scored: false`, and the anomaly opinion's
  reasoning begins `[NOT SCORED -- inputs unavailable]` and states
  explicitly that `score=0.0` means "no score", not "low risk".
- **Neither** → HTTP 422 with an explanation. This is the default on
  purpose: a degraded verdict is never returned by accident.

**Consequence of degraded mode that must not be glossed over:** with the
anomaly opinion's `flag=False`, the coordinator's table reduces to
`context implausible → escalate`, `context plausible → allow`. A degraded
Razorpay transaction can therefore be **auto-allowed on the context and
policy agents alone**. It can never be auto-blocked on a fabricated anomaly
signal — the conservative direction — but "allow" from this path is a
materially weaker statement than "allow" from the PaySim path.

#### The one place a zero balance is still substituted

`policy_agent` coerces the balance fields at the top of `run()`, so `None`
raises before any rule is evaluated — and the agents aren't ours to modify.
`policy_safe_view()` therefore hands policy_agent zeros. This is safe in a
strict, checkable sense rather than a hopeful one: the only rule that reads
a balance is the mule-drain rule, whose first condition is
`transaction_type in ("TRANSFER", "CASH_OUT")`. Every Razorpay payment maps
to `PAYMENT`, so that rule **cannot fire regardless of the balance values**.
The other two rules read only `amount` and `account_age_days`, both of which
map faithfully and keep working. An assertion in `policy_safe_view()` fails
loudly if the type mapping ever changes such that this stops being true.

### 2. `transaction_type` — an axis mismatch, not a lookup gap

PaySim's type (`PAYMENT`/`CASH_OUT`/`CASH_IN`/`TRANSFER`/`DEBIT`) is a
taxonomy of **fund-flow direction**. Razorpay's `method`
(`card`/`netbanking`/`wallet`/`upi`/`emi`/`paylater`) is a taxonomy of
**payment instrument**. These are different axes, and there is no honest
per-method correspondence — netbanking is not "more TRANSFER-like" than UPI
in any sense the model learned. Inventing a mapping would feed the XGBoost
model one-hots that mean something different than they did in training.

What *is* true of every Razorpay payment: it is a customer→merchant debit,
i.e. a `PAYMENT`. So everything maps to `PAYMENT` and the original `method`
is preserved as `source_method` metadata.

**Stated plainly: the five `type_*` one-hot features are constant across
every Razorpay-sourced transaction and contribute zero discriminative
signal.** Combined with the balance gap, a degraded Razorpay transaction
leaves the anomaly model with exactly one informative feature — `amount`.
That is why degraded mode skips the model rather than running it.

### 3. `location_country` — derived, not observed

The Payment entity carries no country. The only geography-adjacent signals
are `international` (bool) and `currency`. Downstream, the *only* consumer
of `location_country` is `context_agent`'s `is_foreign`, which is just
`location_country != profile.home_country`. So the adapter reproduces that
boolean exactly and refuses to invent a country:

- not international → the profile's own `home_country`
- international → `XX` (ISO 3166 user-assigned), meaning "not home", never
  a claim about where

Residual imprecision: Razorpay's `international` flag describes where the
**instrument was issued**, not where the transaction happened. An Indian
resident paying an Indian merchant with a foreign-issued card reads as
`is_foreign = true`.

### 4. `amount_to_typical_ratio` — Razorpay does not serve the history

Razorpay's Customer API exposes name/email/contact only. There is **no
general "fetch this customer's past payments" endpoint** — `Fetch All
Payments` filters by timestamp and pagination, not `customer_id`, and the
customer-identifier variant under Smart Collect is virtual-account scoped,
not a spend history.

So `typical_transaction_amount` has to be maintained by us, keyed by
Razorpay `customer_id` — which is exactly what the existing `user_profiles`
table already is. No profile row and no inline profile → HTTP 404. Note
this means the profile-relative half of the system is only as good as a
history we accumulate ourselves; on a cold start it has nothing.

**Identity caveat:** when `customer_id` is absent the adapter derives an id
from a SHA-256 of `email`/`contact` (hashed, not stored raw — it becomes a
primary key). The same person paying once as a guest with one email and once
with another gets two identities, so their history — and therefore
`amount_to_typical_ratio` — is split. The adapter warns when this happens.

## Other things the adapter warns about

- **Non-settled statuses.** Only `authorized`/`captured`/`refunded` are
  money that moved. A `created` or `failed` payment is an attempt; it is
  still scored if you send it, but flagged, because the agents were trained
  on completed transactions.
- **Partial refunds.** `amount_refunded > 0` is flagged: the adapter scores
  the original amount, not net-of-refund.
- **Assumed currency exponent** for currencies outside the adapter's table.

## Preserved separation

The anomaly/context input split that `feature_engineering.py` enforces is
untouched. The adapter produces one transaction dict and one profile dict;
`build_features()` still reads only transaction-intrinsic fields and
`build_context_signals()` still reads only profile-relative ones. Nothing
about the Razorpay path lets either agent see the other's inputs, so their
disagreement remains a real signal.
