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
