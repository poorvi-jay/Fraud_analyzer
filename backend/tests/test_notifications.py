"""Compliance webhook stub (PRD 7.3).

The property that actually matters here is negative: the notifier must never
be able to break, delay past a timeout, or roll back the verdict it is
reporting on. Most of these tests are about failure paths for that reason.
"""

import pytest

from app import notifications
from app.config import settings


class _RecordingHTTPX:
    """Stands in for the httpx module inside notifications._post."""

    def __init__(self, status_code: int = 200, raises: Exception | None = None):
        self.status_code = status_code
        self.raises = raises
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raises is not None:
            raise self.raises

        class _Response:
            status_code = self.status_code

        return _Response()


@pytest.fixture
def webhook_configured(monkeypatch):
    monkeypatch.setattr(settings, "compliance_webhook_url", "https://compliance.example/hook")
    monkeypatch.setattr(settings, "compliance_webhook_timeout_seconds", 2.5)


def _install_httpx(monkeypatch, stub):
    import sys

    monkeypatch.setitem(sys.modules, "httpx", stub)
    return stub


# --- stub mode (the default, and what the public demo runs) ---


def test_unconfigured_webhook_builds_the_payload_but_sends_nothing(monkeypatch):
    monkeypatch.setattr(settings, "compliance_webhook_url", "")
    result = notifications.notify_transaction_blocked(
        transaction_id="txn-1", user_id="C_1", amount=9000.0, coordinator_reasoning="policy flag"
    )

    assert result.delivered is False
    assert "not configured" in result.reason
    # The payload is still constructed in full, so what a real integration
    # would receive is inspectable (and testable) without a receiver.
    assert result.payload["event"] == "transaction.blocked"
    assert result.payload["transaction_id"] == "txn-1"
    assert result.payload["demo_mode"] is True


def test_payload_carries_no_more_than_the_case_page_already_shows(monkeypatch):
    monkeypatch.setattr(settings, "compliance_webhook_url", "")
    result = notifications.notify_override_rejected(
        review_result_id="rr-1", transaction_id="txn-1", reviewer_id="rev-1", note="confirmed mule account"
    )

    assert set(result.payload) == {
        "event",
        "source",
        "demo_mode",
        "occurred_at",
        "review_result_id",
        "transaction_id",
        "reviewer_id",
        "note",
    }


# --- delivery ---


def test_configured_webhook_posts_the_payload(monkeypatch, webhook_configured):
    stub = _install_httpx(monkeypatch, _RecordingHTTPX())
    result = notifications.notify_transaction_blocked(
        transaction_id="txn-2", user_id="C_2", amount=100.0, coordinator_reasoning="drained balance"
    )

    assert result.delivered is True
    assert len(stub.calls) == 1
    assert stub.calls[0]["url"] == "https://compliance.example/hook"
    assert stub.calls[0]["timeout"] == 2.5
    assert stub.calls[0]["json"]["event"] == "transaction.blocked"


def test_non_2xx_is_reported_not_raised(monkeypatch, webhook_configured):
    _install_httpx(monkeypatch, _RecordingHTTPX(status_code=503))
    result = notifications.notify_transaction_blocked(
        transaction_id="txn-3", user_id="C_3", amount=100.0, coordinator_reasoning="x"
    )

    assert result.delivered is False
    assert "503" in result.reason


def test_transport_failure_is_swallowed(monkeypatch, webhook_configured):
    _install_httpx(monkeypatch, _RecordingHTTPX(raises=OSError("name resolution failed")))
    result = notifications.notify_override_rejected(
        review_result_id="rr-2", transaction_id="txn-4", reviewer_id="rev-1", note="n"
    )

    assert result.delivered is False
    assert "name resolution failed" in result.reason


# --- wiring: the two moments that fire it ---


def test_blocked_transaction_notifies_compliance(client, sample_profile, monkeypatch):
    sent = []
    monkeypatch.setattr(
        notifications,
        "notify_transaction_blocked",
        lambda **kwargs: sent.append(kwargs)
        or notifications.NotificationResult("transaction.blocked", False, "test"),
    )

    resp = client.post(
        "/transactions/review",
        json={
            "user_id": sample_profile.user_id,
            "amount": 8000.0,
            "transaction_type": "TRANSFER",
            "origin_balance_before": 8000.0,
            "origin_balance_after": 0.0,
            "location_country": "FR",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )

    assert resp.json()["review_result"]["final_verdict"] == "block"
    assert len(sent) == 1
    assert sent[0]["user_id"] == sample_profile.user_id
    assert sent[0]["amount"] == 8000.0


def test_an_allowed_transaction_notifies_nobody(client, sample_profile, monkeypatch):
    sent = []
    monkeypatch.setattr(
        notifications,
        "notify_transaction_blocked",
        lambda **kwargs: sent.append(kwargs)
        or notifications.NotificationResult("transaction.blocked", False, "test"),
    )

    resp = client.post(
        "/transactions/review",
        json={
            "user_id": sample_profile.user_id,
            "amount": 150.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 4000.0,
            "origin_balance_after": 3850.0,
            "location_country": "US",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )

    assert resp.json()["review_result"]["final_verdict"] != "block"
    assert sent == []


def test_a_rejection_notifies_compliance_but_an_approval_does_not(
    client, escalated_case, mock_reviewer, monkeypatch
):
    _, review_result = escalated_case
    sent = []
    monkeypatch.setattr(
        notifications,
        "notify_override_rejected",
        lambda **kwargs: sent.append(kwargs)
        or notifications.NotificationResult("review.rejected", False, "test"),
    )

    approve = client.post(
        f"/reviews/{review_result.id}/override", json={"decision": "approve", "note": "looks fine"}
    )
    assert approve.status_code == 200
    assert sent == []

    reject = client.post(
        f"/reviews/{review_result.id}/override", json={"decision": "reject", "note": "confirmed fraud"}
    )
    assert reject.status_code == 200
    assert len(sent) == 1
    assert sent[0]["reviewer_id"] == "test-reviewer-id"
    assert sent[0]["note"] == "confirmed fraud"


def test_a_failing_webhook_does_not_fail_the_request(client, sample_profile, monkeypatch, webhook_configured):
    """The end-to-end version of the ordering guarantee: the verdict is
    committed before the notifier runs, so even a hard transport failure
    leaves a persisted, retrievable case.
    """
    _install_httpx(monkeypatch, _RecordingHTTPX(raises=OSError("connection refused")))

    resp = client.post(
        "/transactions/review",
        json={
            "user_id": sample_profile.user_id,
            "amount": 8000.0,
            "transaction_type": "TRANSFER",
            "origin_balance_before": 8000.0,
            "origin_balance_after": 0.0,
            "location_country": "FR",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["review_result"]["final_verdict"] == "block"
    assert client.get(f"/transactions/{body['id']}").status_code == 200
