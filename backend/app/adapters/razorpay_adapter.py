"""Adapter: Razorpay test-mode Payment object -> this system's internal
transaction schema.

The four agents are NOT modified for Razorpay. Everything provider-specific
is resolved here, so pipeline.py and agents/* keep consuming exactly the
dicts documented in docs/ARCHITECTURE.md's TRANSACTIONS table.

--------------------------------------------------------------------------
WHAT DOES NOT MAP, AND WHY (read this before trusting a Razorpay verdict)
--------------------------------------------------------------------------
PaySim and Razorpay describe different things. PaySim is a *bank account*
simulation: every row is a movement of funds with the origin account's
balance before and after. Razorpay's Payment object is a *merchant
checkout* record: it knows what instrument the customer paid with and how
much, and nothing whatsoever about the customer's account balance. Three
of the anomaly model's nine features live on the far side of that gap.

1. BALANCES / balance_drained_ratio -- NO EQUIVALENT EXISTS.
   The Payment entity has amount, fee, tax, amount_refunded. None of those
   is a balance. There is deliberately no default here:
     - defaulting balances to 0 produces balance_drained_ratio = 0 with
       origin_balance_before = 0, a combination that appears nowhere in
       training, so the model extrapolates off-manifold;
     - defaulting them to `amount` produces balance_drained_ratio = 1.0,
       which is *precisely* real PaySim's fraud signature (a transfer that
       fully drains the origin balance) -- it would make every Razorpay
       payment look like textbook fraud.
   Both are worse than admitting the feature is missing. So: supply a
   BalanceContext from your own ledger, or accept degraded mode, in which
   the anomaly model is NOT SCORED AT ALL rather than scored on invented
   inputs. See adapt_payment().

2. transaction_type -- AXIS MISMATCH, MAPPED CONSERVATIVELY.
   PaySim's type (PAYMENT/CASH_OUT/CASH_IN/TRANSFER/DEBIT) is a taxonomy of
   *fund-flow direction*. Razorpay's `method` (card/netbanking/wallet/upi/
   emi/paylater) is a taxonomy of *payment instrument*. They are not the
   same axis, and there is no honest per-method correspondence: netbanking
   is not "more TRANSFER-like" than UPI in any sense the model learned.
   What is true of every Razorpay payment is that it is a customer->merchant
   debit, i.e. a PAYMENT. So every Razorpay payment maps to PAYMENT and the
   original method is preserved as metadata. Consequence, stated plainly:
   the five type_* one-hot features are CONSTANT across all Razorpay-sourced
   transactions and therefore carry zero discriminative signal. That is a
   real loss of model capacity, not a cosmetic note.

3. location_country -- DERIVED, NOT OBSERVED.
   The Payment entity carries no country. The only geography-adjacent
   signals are `international` (bool) and `currency`. Downstream, the only
   consumer of location_country is context_agent's is_foreign, which is
   just location_country != profile.home_country. So we reproduce that
   boolean exactly and refuse to invent a specific country: not
   international -> the profile's own home country; international -> the
   ISO 3166 user-assigned sentinel below. Residual imprecision worth
   knowing: Razorpay's `international` flag means "an internationally
   issued instrument was used", which is a proxy for where the card was
   issued, not where the transaction happened.

4. amount_to_typical_ratio -- REQUIRES HISTORY RAZORPAY DOES NOT SERVE.
   Razorpay's Customer API exposes name/email/contact only; there is no
   general "fetch this customer's past payments" endpoint (the
   customer-identifier variant under Smart Collect is virtual-account
   scoped, not a spend history). typical_transaction_amount therefore has
   to be maintained by us, keyed by Razorpay customer id -- which is
   exactly what the existing user_profiles table already is. No profile,
   no profile-relative signal.
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agents.base import AgentOpinion

# ISO 3166-1 user-assigned code. Means "somewhere that is not the customer's
# home country" -- never a claim about which country.
FOREIGN_SENTINEL_COUNTRY = "XX"
# Used only if a profile's home_country is itself literally "XX".
FOREIGN_SENTINEL_FALLBACK = "ZZ"

# Razorpay quotes `amount` in the currency's minor unit ("for an amount of
# $1 enter 100"). Most currencies are 2-decimal; these are the exceptions
# we can state confidently. Anything unlisted falls back to 2 and says so.
_MINOR_UNIT_EXPONENT = {
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "XAF": 0, "XOF": 0, "XPF": 0,
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3,
}
_DEFAULT_MINOR_UNIT_EXPONENT = 2

# Only these statuses represent money that actually moved. `created` and
# `failed` are attempts, not transactions.
SETTLED_STATUSES = frozenset({"authorized", "captured", "refunded"})

# Every Razorpay payment is a customer->merchant debit. See note 2 above.
RAZORPAY_TRANSACTION_TYPE = "PAYMENT"


class RazorpayAdapterError(ValueError):
    """The payload cannot be adapted without inventing data."""


@dataclass(frozen=True)
class BalanceContext:
    """The caller's own ledger view of the paying account, supplied out of
    band because Razorpay does not carry it. Both figures are in major
    currency units (rupees, not paise) -- unlike Razorpay's `amount`.
    """

    origin_balance_before: float
    origin_balance_after: float


@dataclass(frozen=True)
class AdaptedPayment:
    transaction: dict
    user_id: str
    feature_availability: dict[str, bool]
    warnings: list[str] = field(default_factory=list)
    source_method: str | None = None

    @property
    def anomaly_scorable(self) -> bool:
        """False when the balance features are missing, i.e. when scoring
        the anomaly model would mean scoring it on fabricated inputs."""
        return self.feature_availability["balance_features"]


def to_major_units(amount_subunits: int, currency: str) -> tuple[float, str | None]:
    """Razorpay `amount` (minor units) -> major units. Returns the value and
    an optional warning when the currency's exponent had to be assumed."""
    code = (currency or "INR").upper()
    warning = None
    if code in _MINOR_UNIT_EXPONENT:
        exponent = _MINOR_UNIT_EXPONENT[code]
    else:
        exponent = _DEFAULT_MINOR_UNIT_EXPONENT
        warning = (
            f"currency {code!r} is not in the adapter's minor-unit table; assumed "
            f"{_DEFAULT_MINOR_UNIT_EXPONENT} decimal places. Verify before trusting the amount."
        )
    return float(amount_subunits) / (10 ** exponent), warning


def resolve_user_id(payment: dict) -> tuple[str, str | None]:
    """Map a Razorpay payer onto our user_profiles.user_id key.

    Prefers customer_id, which is stable and non-identifying. Falls back to
    email/contact -- but those are PII, so they are hashed rather than
    stored as a primary key. Returns (user_id, warning).
    """
    customer_id = payment.get("customer_id")
    if customer_id:
        return str(customer_id), None

    for field_name in ("email", "contact"):
        value = payment.get(field_name)
        if value:
            digest = hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()[:24]
            return f"rzp_anon_{digest}", (
                f"payment has no customer_id; identity derived from a hash of {field_name}. "
                "Two payments from the same person with different contact details will not "
                "share a profile, so their history -- and therefore amount_to_typical_ratio "
                "-- will be split across identities."
            )

    raise RazorpayAdapterError(
        "payment carries no customer_id, email, or contact -- there is no way to associate it "
        "with a user profile, and amount_to_typical_ratio cannot be computed without one."
    )


def _derive_location_country(payment: dict, home_country: str) -> tuple[str, str | None]:
    if not payment.get("international"):
        return home_country, None
    sentinel = (
        FOREIGN_SENTINEL_FALLBACK
        if home_country.upper() == FOREIGN_SENTINEL_COUNTRY
        else FOREIGN_SENTINEL_COUNTRY
    )
    return sentinel, (
        f"location_country set to the sentinel {sentinel!r}: Razorpay reports the payment as "
        "international but never says which country. This reproduces context_agent's is_foreign "
        "boolean correctly and asserts nothing about location. Note that Razorpay's "
        "`international` flag describes where the instrument was issued, not where the "
        "transaction occurred."
    )


def adapt_payment(
    payment: dict,
    *,
    home_country: str,
    balance_context: BalanceContext | None = None,
) -> AdaptedPayment:
    """Transform a Razorpay Payment object into the internal transaction dict
    the four agents already consume.

    home_country comes from the matched user profile and is used only to
    derive location_country (see note 3 in the module docstring).

    balance_context is the caller's own ledger reading. When it is None the
    result is marked not anomaly_scorable -- the caller is expected to skip
    anomaly scoring rather than let the model run on invented balances.
    """
    if payment.get("entity") not in (None, "payment"):
        raise RazorpayAdapterError(
            f"expected a Razorpay payment entity, got entity={payment.get('entity')!r}"
        )
    if "amount" not in payment:
        raise RazorpayAdapterError("payment has no `amount` field")

    warnings: list[str] = []

    amount, amount_warning = to_major_units(payment["amount"], payment.get("currency", "INR"))
    if amount_warning:
        warnings.append(amount_warning)

    user_id, identity_warning = resolve_user_id(payment)
    if identity_warning:
        warnings.append(identity_warning)

    status = payment.get("status")
    if status is not None and status not in SETTLED_STATUSES:
        warnings.append(
            f"payment status is {status!r}, which is an attempt rather than settled money "
            f"movement (settled statuses: {sorted(SETTLED_STATUSES)}). Scoring it anyway, but "
            "the agents were trained on completed transactions."
        )
    if payment.get("amount_refunded"):
        warnings.append(
            f"payment has amount_refunded={payment['amount_refunded']}; the adapter scores the "
            "original amount, not the net-of-refund amount."
        )

    location_country, location_warning = _derive_location_country(payment, home_country)
    if location_warning:
        warnings.append(location_warning)

    created_at = payment.get("created_at")
    if created_at is None:
        occurred_at = datetime.now(timezone.utc).replace(tzinfo=None)
        warnings.append("payment has no created_at; used the current time as occurred_at.")
    else:
        # Razorpay sends a UNIX timestamp in seconds, UTC.
        occurred_at = datetime.fromtimestamp(int(created_at), tz=timezone.utc).replace(tzinfo=None)

    method = payment.get("method")
    warnings.append(
        f"transaction_type forced to {RAZORPAY_TRANSACTION_TYPE!r} (Razorpay method={method!r}): "
        "Razorpay's instrument taxonomy and PaySim's fund-flow taxonomy are different axes. "
        "The five type_* one-hot features are therefore constant for every Razorpay-sourced "
        "transaction and contribute no discriminative signal to the anomaly model."
    )

    if balance_context is None:
        balance_features = False
        origin_balance_before = None
        origin_balance_after = None
        warnings.append(
            "NO BALANCE CONTEXT SUPPLIED. Razorpay's Payment object carries no account balance, "
            "and origin_balance_before / origin_balance_after / balance_drained_ratio are 3 of "
            "the anomaly model's 9 features. They are left unset rather than defaulted -- "
            "anomaly_agent must be skipped for this transaction, not fed invented values."
        )
    else:
        balance_features = True
        origin_balance_before = float(balance_context.origin_balance_before)
        origin_balance_after = float(balance_context.origin_balance_after)

    transaction = {
        "amount": amount,
        "transaction_type": RAZORPAY_TRANSACTION_TYPE,
        "origin_balance_before": origin_balance_before,
        "origin_balance_after": origin_balance_after,
        "location_country": location_country,
        "occurred_at": occurred_at,
        # Provenance metadata. The agents index the keys above by name and
        # ignore anything extra, so these ride along harmlessly and keep the
        # Razorpay origin visible to anything that inspects the dict.
        "source": "razorpay",
        "source_payment_id": payment.get("id"),
        "source_method": method,
        "source_status": status,
        "source_currency": (payment.get("currency") or "INR").upper(),
    }

    return AdaptedPayment(
        transaction=transaction,
        user_id=user_id,
        feature_availability={
            # Amount and the profile-relative signals survive the mapping intact.
            "amount": True,
            "profile_relative_signals": True,
            # These do not.
            "balance_features": balance_features,
            "transaction_type_signal": False,
            "observed_location_country": False,
        },
        warnings=warnings,
        source_method=method,
    )


def policy_safe_view(transaction: dict) -> dict:
    """A copy of a balance-less transaction dict that policy_agent can read.

    Needed because policy_agent unconditionally coerces the balance fields at
    the top of run(), so `None` raises before any rule is evaluated -- and
    policy_agent is not ours to modify.

    Substituting zeros here is safe in the strict sense that the substituted
    values are PROVABLY UNREACHABLE, not merely unlikely to matter. The only
    rule that reads a balance is the mule-drain rule, and its first condition
    is `transaction_type in ("TRANSFER", "CASH_OUT")`. Every Razorpay payment
    maps to PAYMENT (see note 2 in the module docstring), so that rule cannot
    fire regardless of what the balances say. The other two rules -- the
    reporting threshold and the new-account rule -- read only `amount` and
    `account_age_days`, both of which map faithfully, and both of which keep
    working here. That is why this is a view for policy_agent specifically
    and not a general-purpose default: anomaly_agent gets no such view, it
    simply doesn't run.

    The assertion below keeps this argument honest. If the type mapping ever
    changes so that a Razorpay transaction could match the mule rule, this
    fails loudly instead of quietly feeding the rule invented balances.
    """
    assert transaction["transaction_type"] not in ("TRANSFER", "CASH_OUT"), (
        "policy_safe_view assumes the mule-drain rule is gated out by transaction_type; "
        f"got transaction_type={transaction['transaction_type']!r}, which the rule CAN match. "
        "The zero balances below would now reach a live rule -- fix the mapping."
    )
    return {**transaction, "origin_balance_before": 0.0, "origin_balance_after": 0.0}


def unavailable_anomaly_opinion(reason: str) -> AgentOpinion:
    """A stand-in for anomaly_agent's opinion when its inputs are missing.

    Deliberately built HERE and not inside anomaly_agent: the agent is not
    modified, and this object is explicitly labelled as an absence of a
    score rather than a score of zero. score=0.0/flag=False is the
    conservative direction -- it can never drive an automatic block off a
    fabricated signal -- but it is NOT a statement that the transaction
    looks fine, and the reasoning text says so.
    """
    return AgentOpinion(
        agent_name="anomaly_agent",
        score=0.0,
        flag=False,
        reasoning=(
            "[NOT SCORED -- inputs unavailable] The anomaly model was not run. "
            f"{reason} score=0.0 here means 'no score', not 'low risk'; treat this verdict as "
            "resting on the context and policy agents alone."
        ),
    )
