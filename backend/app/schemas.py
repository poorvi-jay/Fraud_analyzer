from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
