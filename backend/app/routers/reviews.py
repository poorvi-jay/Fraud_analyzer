from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.auth import require_reviewer
from app.db import get_db
from app.models import HumanReview, ReviewResult
from app.schemas import OverrideRequest, ReviewResultOut

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/{review_result_id}/override", response_model=ReviewResultOut)
def override_review(
    review_result_id: str,
    payload: OverrideRequest,
    reviewer_id: str = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    review_result = db.get(ReviewResult, review_result_id, options=[joinedload(ReviewResult.human_reviews)])
    if review_result is None:
        raise HTTPException(status_code=404, detail=f"No review result with id={review_result_id!r}")

    if review_result.final_verdict != "escalate":
        raise HTTPException(
            status_code=400,
            detail=f"Only escalated cases can be overridden (this case's verdict is {review_result.final_verdict!r}).",
        )

    human_review = HumanReview(
        review_result_id=review_result.id,
        reviewer_id=reviewer_id,
        decision=payload.decision,
        note=payload.note,
    )
    db.add(human_review)
    db.commit()
    db.refresh(review_result)
    return review_result
