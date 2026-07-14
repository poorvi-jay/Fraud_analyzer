"""SQLAlchemy models mirroring docs/ARCHITECTURE.md's ER diagram exactly.

String UUID primary keys (not Postgres-native UUID/JSONB types) so the same
schema runs unchanged on local SQLite and on Supabase Postgres later --
just point DATABASE_URL at the real database (see supabase/schema.sql for
the equivalent DDL there).
"""
import uuid
from datetime import datetime, date

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_created: Mapped[date] = mapped_column(Date)
    home_country: Mapped[str] = mapped_column(String)
    typical_transaction_amount: Mapped[float] = mapped_column(Float)
    travel_frequency: Mapped[str] = mapped_column(String)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.user_id"))
    amount: Mapped[float] = mapped_column(Float)
    transaction_type: Mapped[str] = mapped_column(String)
    origin_balance_before: Mapped[float] = mapped_column(Float)
    origin_balance_after: Mapped[float] = mapped_column(Float)
    location_country: Mapped[str] = mapped_column(String)
    is_fraud_ground_truth: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped["UserProfile"] = relationship(back_populates="transactions")
    opinions: Mapped[list["AgentOpinion"]] = relationship(back_populates="transaction")
    review_result: Mapped["ReviewResult | None"] = relationship(back_populates="transaction", uselist=False)


class AgentOpinion(Base):
    __tablename__ = "agent_opinions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    agent_name: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float)
    flag: Mapped[bool] = mapped_column(Boolean)
    reasoning: Mapped[str] = mapped_column(String)

    transaction: Mapped["Transaction"] = relationship(back_populates="opinions")


class ReviewResult(Base):
    __tablename__ = "review_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    final_verdict: Mapped[str] = mapped_column(String)  # allow | escalate | block
    coordinator_reasoning: Mapped[str] = mapped_column(String)

    transaction: Mapped["Transaction"] = relationship(back_populates="review_result")
    human_reviews: Mapped[list["HumanReview"]] = relationship(back_populates="review_result")


class HumanReview(Base):
    """Phase 2 override flow: a reviewer's decision on an escalated case."""

    __tablename__ = "human_reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    review_result_id: Mapped[str] = mapped_column(ForeignKey("review_results.id"))
    reviewer_id: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)  # approve | reject
    note: Mapped[str] = mapped_column(String)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    review_result: Mapped["ReviewResult"] = relationship(back_populates="human_reviews")
