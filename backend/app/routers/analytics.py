import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/analytics", tags=["analytics"])

REPORT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "ml" / "reports" / "baseline_comparison.json"


@router.get("/evaluation-summary")
def evaluation_summary():
    if not REPORT_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No baseline comparison report yet -- run ml/evaluate_baseline.py first.",
        )
    return json.loads(REPORT_PATH.read_text())
