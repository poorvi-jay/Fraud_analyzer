"""Compliance notification stub (PRD 7.3 stretch).

Fires when the pipeline auto-blocks a transaction, or when a reviewer
rejects an escalated case -- the two moments a real fraud operation would
have to tell a compliance team about.

Deliberately a *stub*: with no COMPLIANCE_WEBHOOK_URL configured (the
default, including on the public demo) nothing leaves the process. The
payload is built and logged either way, so the shape a real integration
would receive is visible and testable without standing up a receiver.

Three properties this module guarantees, because a notifier that can break
the thing it observes is worse than no notifier:

1. It never raises. Every failure -- unreachable host, timeout, non-2xx,
   malformed URL -- is caught and returned as a NotificationResult.
2. It is called only *after* the database commit it describes, so a
   delivery failure can never roll back a verdict or an override.
3. It carries no PII beyond what the demo already exposes publicly on the
   case detail page (ids, amount, verdict, reasoning) -- see PRD 4.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationResult:
    """What the notifier did. Returned rather than raised so callers can
    surface it (or ignore it) without a try/except at every call site.
    """

    event: str
    delivered: bool
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)


def _build_payload(event: str, **fields: Any) -> dict[str, Any]:
    return {
        "event": event,
        "source": "fraud-investigation-squad",
        "demo_mode": True,  # PRD 4: never real payment data, and says so on the wire
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }


def _post(payload: dict[str, Any]) -> NotificationResult:
    event = payload["event"]
    if not settings.compliance_webhook_url:
        logger.info("compliance webhook not configured; would have sent: %s", payload)
        return NotificationResult(
            event=event,
            delivered=False,
            reason="COMPLIANCE_WEBHOOK_URL not configured -- payload logged only",
            payload=payload,
        )

    try:
        import httpx

        response = httpx.post(
            settings.compliance_webhook_url,
            json=payload,
            timeout=settings.compliance_webhook_timeout_seconds,
        )
        if response.status_code >= 400:
            logger.warning(
                "compliance webhook rejected %s: HTTP %s", event, response.status_code
            )
            return NotificationResult(
                event=event,
                delivered=False,
                reason=f"webhook returned HTTP {response.status_code}",
                payload=payload,
            )
    except Exception as exc:  # noqa: BLE001 -- see module docstring, property 1
        logger.warning("compliance webhook failed for %s: %s", event, exc)
        return NotificationResult(
            event=event, delivered=False, reason=f"webhook call failed: {exc}", payload=payload
        )

    logger.info("compliance webhook delivered %s", event)
    return NotificationResult(event=event, delivered=True, reason="delivered", payload=payload)


def notify_transaction_blocked(
    *, transaction_id: str, user_id: str, amount: float, coordinator_reasoning: str
) -> NotificationResult:
    """The pipeline auto-blocked a transaction with no human in the loop."""
    return _post(
        _build_payload(
            "transaction.blocked",
            transaction_id=transaction_id,
            user_id=user_id,
            amount=amount,
            final_verdict="block",
            coordinator_reasoning=coordinator_reasoning,
        )
    )


def notify_override_rejected(
    *, review_result_id: str, transaction_id: str, reviewer_id: str, note: str
) -> NotificationResult:
    """A human reviewer rejected an escalated case -- i.e. confirmed it as
    fraud, which is the override direction compliance cares about. An
    `approve` is a human clearing a case and raises nothing.
    """
    return _post(
        _build_payload(
            "review.rejected",
            review_result_id=review_result_id,
            transaction_id=transaction_id,
            reviewer_id=reviewer_id,
            note=note,
        )
    )
