from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.agents.pipeline import UnknownUserError, run_pipeline, run_simulation
from app.db import get_db
from app.models import ReviewResult, Transaction, UserProfile
from app.rate_limit import limiter
from app.config import settings
from app.schemas import (
    ExampleUserOut,
    SimulateRequest,
    SimulateResponse,
    TransactionDetail,
    TransactionListItem,
    TransactionReviewRequest,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/review", response_model=TransactionDetail)
@limiter.limit(settings.review_rate_limit)
def review_transaction(request: Request, payload: TransactionReviewRequest, db: Session = Depends(get_db)):
    try:
        txn = run_pipeline(db, payload)
    except UnknownUserError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return txn


@router.post("/simulate", response_model=SimulateResponse)
@limiter.limit(settings.review_rate_limit)
def simulate_transaction(request: Request, payload: SimulateRequest, db: Session = Depends(get_db)):
    """Playground endpoint: runs the full pipeline but never persists
    anything (see run_simulation) so visitors can try arbitrary scenarios
    without polluting the demo queue.
    """
    try:
        result = run_simulation(db, payload)
    except UnknownUserError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return SimulateResponse(
        opinions=[result.anomaly, result.context, result.policy],
        final_verdict=result.verdict.final_verdict,
        coordinator_reasoning=result.verdict.coordinator_reasoning,
    )


@router.get("/example-users", response_model=list[ExampleUserOut])
def list_example_users(limit: int = Query(default=6, le=20), db: Session = Depends(get_db)):
    """A handful of real seeded users, for the playground's 'use an existing
    profile' picker.
    """
    stmt = select(UserProfile).order_by(UserProfile.user_id).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("", response_model=list[TransactionListItem])
def list_transactions(
    verdict: str | None = Query(default=None, description="Filter by allow | escalate | block"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Transaction)
        .options(joinedload(Transaction.review_result))
        .order_by(Transaction.occurred_at.desc())
    )
    if verdict:
        stmt = stmt.join(ReviewResult).where(ReviewResult.final_verdict == verdict)
    stmt = stmt.offset(offset).limit(limit)

    rows = db.execute(stmt).unique().scalars().all()
    return [
        TransactionListItem(
            id=txn.id,
            user_id=txn.user_id,
            amount=txn.amount,
            transaction_type=txn.transaction_type,
            location_country=txn.location_country,
            occurred_at=txn.occurred_at,
            final_verdict=txn.review_result.final_verdict if txn.review_result else None,
        )
        for txn in rows
    ]


@router.get("/{transaction_id}", response_model=TransactionDetail)
def get_transaction(transaction_id: str, db: Session = Depends(get_db)):
    txn = db.get(
        Transaction,
        transaction_id,
        options=[
            joinedload(Transaction.opinions),
            joinedload(Transaction.review_result).joinedload(ReviewResult.human_reviews),
        ],
    )
    if txn is None:
        raise HTTPException(status_code=404, detail=f"No transaction with id={transaction_id!r}")
    return txn
