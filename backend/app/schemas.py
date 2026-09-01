from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TransactionReviewRequest(BaseModel):
    user_id: str
    amount: float
    transaction_type: str
    origin_balance_before: float
    origin_balance_after: float
    location_country: str
    occurred_at: datetime | None = None
    # Only populated by ml/seed_demo_queue.py and the evaluation harness,
    # never by a real reviewer -- live demo transactions have no ground truth.
    is_fraud_ground_truth: bool | None = None


class AgentOpinionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    score: float
    flag: bool
    reasoning: str


class OverrideRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str


class HumanReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: str
    note: str
    reviewer_id: str
    reviewed_at: datetime


class ReviewResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    final_verdict: str
    coordinator_reasoning: str
    human_reviews: list[HumanReviewOut] = []


class TransactionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    amount: float
    transaction_type: str
    location_country: str
    occurred_at: datetime
    final_verdict: str | None = None


class TransactionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    amount: float
    transaction_type: str
    origin_balance_before: float
    origin_balance_after: float
    location_country: str
    occurred_at: datetime
    is_fraud_ground_truth: bool | None
    opinions: list[AgentOpinionOut]
    review_result: ReviewResultOut | None


class ExampleUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    home_country: str
    typical_transaction_amount: float
    travel_frequency: str
    account_created: date


class InlineProfile(BaseModel):
    """A visitor-authored stand-in for a UserProfile row, used by the
    playground so a transaction can be judged without an existing seeded
    user -- see SimulateRequest.
    """

    home_country: str
    typical_transaction_amount: float = Field(gt=0)
    travel_frequency: Literal["never", "rare", "frequent"]
    account_age_days: int = Field(ge=0)


class SimulateRequest(BaseModel):
    """Playground input: run the full agent pipeline against either an
    existing seeded user (user_id) or a visitor-built profile (profile),
    without persisting anything -- see run_simulation().
    """

    user_id: str | None = None
    profile: InlineProfile | None = None
    amount: float
    transaction_type: str
    origin_balance_before: float
    origin_balance_after: float
    location_country: str
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def _require_profile_source(self):
        if not self.user_id and self.profile is None:
            raise ValueError("either user_id or profile must be provided")
        return self


class SimulateResponse(BaseModel):
    opinions: list[AgentOpinionOut]
    final_verdict: str
    coordinator_reasoning: str


# --- Razorpay ingestion (see app/adapters/razorpay_adapter.py) ---


class RazorpayPaymentIn(BaseModel):
    """Razorpay's test-mode Payment entity, as returned by
    GET /v1/payments/:id.

    Permissive by design (extra="allow"): Razorpay adds fields over time and
    sends method-specific blocks (card, upi, acquirer_data) we don't consume.
    Rejecting unknown keys would break on a live payload for no benefit. The
    fields declared here are the ones the adapter actually reads, so they
    show up in the OpenAPI docs; `amount` is the only hard requirement.
    """

    model_config = ConfigDict(extra="allow")

    # Razorpay quotes amount in the currency's MINOR unit (paise for INR):
    # "for an amount of $1 enter 100". The adapter converts.
    amount: int = Field(ge=0, description="Amount in currency subunits (paise for INR)")
    id: str | None = None
    entity: str | None = None
    currency: str = "INR"
    status: str | None = Field(
        default=None, description="created | authorized | captured | refunded | failed"
    )
    method: str | None = Field(
        default=None, description="card | netbanking | wallet | upi | emi | paylater"
    )
    order_id: str | None = None
    customer_id: str | None = None
    email: str | None = None
    contact: str | None = None
    international: bool | None = None
    captured: bool | None = None
    amount_refunded: int | None = None
    description: str | None = None
    created_at: int | None = Field(default=None, description="UNIX timestamp, seconds")


class RazorpayBalanceContext(BaseModel):
    """The paying account's balance around this payment, in MAJOR currency
    units (rupees, not paise).

    Razorpay has no equivalent field -- this has to come from the caller's
    own ledger. Without it, 3 of the anomaly model's 9 features have no
    source; see RazorpaySimulateRequest.allow_degraded.
    """

    origin_balance_before: float = Field(ge=0)
    origin_balance_after: float = Field(ge=0)


class RazorpaySimulateRequest(BaseModel):
    payment: RazorpayPaymentIn
    # Same two profile sources as SimulateRequest: an inline visitor-authored
    # profile, or (when omitted) the seeded user_profiles row matching the
    # identity the adapter derives from the payment.
    profile: InlineProfile | None = None
    balance_context: RazorpayBalanceContext | None = None
    allow_degraded: bool = Field(
        default=False,
        description=(
            "Opt in to scoring without balance_context. The anomaly model is then NOT run at "
            "all -- rather than run on invented balances -- and the verdict rests on the "
            "context and policy agents alone. Default false: the request is refused instead, "
            "so a degraded score is never returned by accident."
        ),
    )


class RazorpaySimulateResponse(BaseModel):
    opinions: list[AgentOpinionOut]
    final_verdict: str
    coordinator_reasoning: str
    user_id: str = Field(description="Identity the adapter mapped this payment onto")
    anomaly_scored: bool = Field(
        description="False when the anomaly model was skipped for lack of balance inputs"
    )
    feature_availability: dict[str, bool] = Field(
        description="Which feature groups survived the Razorpay mapping intact"
    )
    adapter_warnings: list[str] = Field(
        default_factory=list,
        description="Everything the adapter had to derive, assume, or drop. Read these.",
    )
