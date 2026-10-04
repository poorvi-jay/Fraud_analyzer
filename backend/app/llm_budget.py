"""Persistent, per-project spend caps for live LLM calls.

Why this exists alongside llm_usage.tracker: that tracker is in-memory and
resets whenever the process restarts, which on Render's free tier is every
cold start. It can log usage but it cannot enforce a monthly cap. This
module reads and writes the llm_daily_spend table instead, so the cap holds
across restarts, and across the API and the ml/ scripts when they share a
DATABASE_URL.

Guarantees and their limits:

  * Fails CLOSED. If the ledger can't be read, no live call is made.
  * Before each call, checks spent + one estimated call against both the
    daily and monthly cap.
  * Writes are serialised through one background worker. Concurrent
    requests can still both pass the check before either write lands, so
    the cap can be overshot by roughly (concurrent requests x one call's
    cost) -- about half a cent at FastAPI's default threadpool size. That
    is the accepted precision; the cap is not a transactional reservation.
  * Days and months are UTC.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.llm_usage import estimate_cost_usd

logger = logging.getLogger(__name__)

# One worker: serialises every ledger write in this process. That removes
# write contention between concurrent requests, and -- the reason it has to
# be a separate thread at all -- lets a write wait for SQLite's lock while
# run_pipeline, on the calling thread, still holds an open transaction. A
# synchronous write there would block on a lock held by its own request.
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-ledger")


class LLMBudgetExceeded(RuntimeError):
    """A spend cap would be crossed, or the ledger couldn't be read."""


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def spend_snapshot() -> tuple[float, float]:
    """-> (spent today, spent this month) in USD, straight from the ledger."""
    from app.db import SessionLocal
    from app.models import LLMDailySpend

    today = _today()
    month_start, month_end = _month_bounds(today)
    with SessionLocal() as db:
        day_cost = db.execute(
            select(LLMDailySpend.cost_usd).where(LLMDailySpend.day == today)
        ).scalar_one_or_none() or 0.0
        month_cost = db.execute(
            select(func.coalesce(func.sum(LLMDailySpend.cost_usd), 0.0)).where(
                LLMDailySpend.day >= month_start, LLMDailySpend.day < month_end
            )
        ).scalar_one()
    return float(day_cost), float(month_cost)


def remaining() -> tuple[float, float]:
    """-> (USD left today, USD left this month), floored at zero."""
    day_cost, month_cost = spend_snapshot()
    return (
        max(settings.llm_daily_budget_usd - day_cost, 0.0),
        max(settings.llm_monthly_budget_usd - month_cost, 0.0),
    )


def check_budget(model: str, n_calls: int = 1) -> None:
    """Raise LLMBudgetExceeded unless n_calls more would fit under both caps."""
    try:
        day_cost, month_cost = spend_snapshot()
    except Exception as exc:
        raise LLMBudgetExceeded(f"could not read the spend ledger ({exc}); failing closed") from exc

    next_cost = estimate_cost_usd(model, n_calls)
    if day_cost + next_cost > settings.llm_daily_budget_usd:
        raise LLMBudgetExceeded(
            f"daily LLM budget reached (${day_cost:.4f} of ${settings.llm_daily_budget_usd:.2f} spent today, UTC)"
        )
    if month_cost + next_cost > settings.llm_monthly_budget_usd:
        raise LLMBudgetExceeded(
            f"monthly LLM budget reached (${month_cost:.4f} of ${settings.llm_monthly_budget_usd:.2f} spent this month)"
        )


def _write(day: date, input_tokens: int, output_tokens: int, cost: float) -> None:
    from app.db import SessionLocal
    from app.models import LLMDailySpend

    increment = update(LLMDailySpend).where(LLMDailySpend.day == day).values(
        calls=LLMDailySpend.calls + 1,
        input_tokens=LLMDailySpend.input_tokens + input_tokens,
        output_tokens=LLMDailySpend.output_tokens + output_tokens,
        cost_usd=LLMDailySpend.cost_usd + cost,
    )
    # Increment in SQL, not read-modify-write in Python, so a second process
    # writing the same ledger (a local seed run against the production
    # database, say) can't lose an update.
    with SessionLocal() as db:
        if db.execute(increment).rowcount:
            db.commit()
            return
        db.add(LLMDailySpend(day=day, calls=1, input_tokens=input_tokens,
                             output_tokens=output_tokens, cost_usd=cost))
        try:
            db.commit()
        except IntegrityError:
            # Another process inserted today's row between our UPDATE and
            # INSERT. It exists now, so the increment will land.
            db.rollback()
            db.execute(increment)
            db.commit()


def _log_failure(future) -> None:
    exc = future.exception()
    if exc is not None:
        # Losing a write undercounts spend, which weakens the cap. Loud on
        # purpose -- this should be investigated, not ignored.
        logger.error("llm_daily_spend write FAILED, cap may undercount: %s", exc)


def record_spend(input_tokens: int, output_tokens: int, cost: float) -> None:
    """Queue a ledger write for one completed call. Returns immediately."""
    _writer.submit(_write, _today(), input_tokens, output_tokens, cost).add_done_callback(_log_failure)


def flush() -> None:
    """Block until queued ledger writes have landed. For scripts that report
    ledger totals at the end of a run."""
    _writer.submit(lambda: None).result()
