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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.llm_usage import UsageTotals, estimate_cost_usd

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


def usage_snapshot() -> dict:
    """Full ledger totals for today and this month, with the caps applied.

    Exists so spend is observable from outside the process. The caps were
    enforceable but invisible before this: short of querying the database
    by hand there was no way to see what had been spent.
    """
    from app.db import SessionLocal
    from app.models import LLMDailySpend

    today = _today()
    month_start, month_end = _month_bounds(today)
    totals = (
        func.coalesce(func.sum(LLMDailySpend.calls), 0),
        func.coalesce(func.sum(LLMDailySpend.input_tokens), 0),
        func.coalesce(func.sum(LLMDailySpend.output_tokens), 0),
        func.coalesce(func.sum(LLMDailySpend.cost_usd), 0.0),
    )
    with SessionLocal() as db:
        day_row = db.execute(
            select(*totals).where(LLMDailySpend.day == today)
        ).one()
        month_row = db.execute(
            select(*totals).where(LLMDailySpend.day >= month_start, LLMDailySpend.day < month_end)
        ).one()

    def _period(row, cap):
        calls, tokens_in, tokens_out, cost = row
        return {
            "calls": int(calls),
            "input_tokens": int(tokens_in),
            "output_tokens": int(tokens_out),
            "cost_usd": round(float(cost), 6),
            "budget_usd": cap,
            "remaining_usd": round(max(cap - float(cost), 0.0), 6),
            "exhausted": float(cost) >= cap,
        }

    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model if settings.llm_provider != "mock" else None,
        "as_of_utc": today.isoformat(),
        "today": _period(day_row, settings.llm_daily_budget_usd),
        "month_to_date": _period(month_row, settings.llm_monthly_budget_usd),
    }


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

    # Spend billed but not yet written counts too. Without this, a batch job
    # whose ledger writes are lagging would keep passing the check on a stale
    # total and blow straight through the cap.
    unwritten = pending_cost()
    day_cost += unwritten
    month_cost += unwritten

    next_cost = estimate_cost_usd(model, n_calls)
    if day_cost + next_cost > settings.llm_daily_budget_usd:
        raise LLMBudgetExceeded(
            f"daily LLM budget reached (${day_cost:.4f} of ${settings.llm_daily_budget_usd:.2f} spent today, UTC)"
        )
    if month_cost + next_cost > settings.llm_monthly_budget_usd:
        raise LLMBudgetExceeded(
            f"monthly LLM budget reached (${month_cost:.4f} of ${settings.llm_monthly_budget_usd:.2f} spent this month)"
        )


WRITE_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 0.5

# Spend that has been billed but not yet written to the ledger. Held here so
# two things stay true: a transient write failure never loses the amount, and
# check_budget counts it immediately -- an unwritten dollar is still spent.
_pending_lock = threading.Lock()
_pending = UsageTotals()


def _add_pending(calls: int, input_tokens: int, output_tokens: int, cost: float) -> None:
    with _pending_lock:
        _pending.calls += calls
        _pending.input_tokens += input_tokens
        _pending.output_tokens += output_tokens
        _pending.cost_usd += cost


def pending_cost() -> float:
    with _pending_lock:
        return _pending.cost_usd


def reset_pending() -> None:
    """Discard unwritten spend. Only for tests -- in a running process this
    would lose real spend that the cap is counting on."""
    with _pending_lock:
        _pending.calls = _pending.input_tokens = _pending.output_tokens = 0
        _pending.cost_usd = 0.0


def _write_batch(day: date, batch: UsageTotals) -> None:
    from app.db import SessionLocal
    from app.models import LLMDailySpend

    increment = update(LLMDailySpend).where(LLMDailySpend.day == day).values(
        calls=LLMDailySpend.calls + batch.calls,
        input_tokens=LLMDailySpend.input_tokens + batch.input_tokens,
        output_tokens=LLMDailySpend.output_tokens + batch.output_tokens,
        cost_usd=LLMDailySpend.cost_usd + batch.cost_usd,
    )
    # Increment in SQL, not read-modify-write in Python, so a second process
    # writing the same ledger (a local seed run against the production
    # database, say) can't lose an update.
    with SessionLocal() as db:
        if db.execute(increment).rowcount:
            db.commit()
            return
        db.add(LLMDailySpend(day=day, calls=batch.calls, input_tokens=batch.input_tokens,
                             output_tokens=batch.output_tokens, cost_usd=batch.cost_usd))
        try:
            db.commit()
        except IntegrityError:
            # Another process inserted today's row between our UPDATE and
            # INSERT. It exists now, so the increment will land.
            db.rollback()
            db.execute(increment)
            db.commit()


def _drain() -> None:
    """Write everything pending as ONE row update, retrying on contention.

    Coalescing matters as much as retrying: a batch job generates a call
    every second or two, and one write attempt each is what let SQLite starve
    them. Draining the accumulated total means far fewer attempts, each
    carrying more, so a single success catches the ledger up completely.
    """
    with _pending_lock:
        if _pending.calls == 0:
            return
        batch = UsageTotals(_pending.calls, _pending.input_tokens,
                            _pending.output_tokens, _pending.cost_usd)
        _pending.calls = _pending.input_tokens = _pending.output_tokens = 0
        _pending.cost_usd = 0.0

    day = _today()
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            _write_batch(day, batch)
            return
        except Exception as exc:
            if attempt == WRITE_ATTEMPTS:
                # Put it back rather than dropping it: it stays counted
                # against the cap via pending_cost(), and the next drain
                # will try again. Spend is never silently discarded.
                _add_pending(batch.calls, batch.input_tokens, batch.output_tokens, batch.cost_usd)
                logger.error(
                    "llm_daily_spend write failed after %d attempts (%s); %d call(s) "
                    "worth $%.6f stay pending and still count against the cap",
                    WRITE_ATTEMPTS, exc, batch.calls, batch.cost_usd,
                )
                return
            logger.warning("llm_daily_spend write contended (attempt %d/%d): %s",
                           attempt, WRITE_ATTEMPTS, exc)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)


def record_spend(input_tokens: int, output_tokens: int, cost: float) -> None:
    """Account for one completed call. Returns immediately; the ledger write
    happens on the writer thread."""
    _add_pending(1, input_tokens, output_tokens, cost)
    _writer.submit(_drain)


def flush() -> None:
    """Block until pending spend has been written. For scripts that report
    ledger totals at the end of a run."""
    _writer.submit(_drain).result()
