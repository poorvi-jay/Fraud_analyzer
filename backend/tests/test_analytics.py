"""Analytics endpoints share the session-scoped DB with the rest of the
suite (see conftest.py), so these tests assert on *deltas* caused by
transactions they create themselves, rather than exact totals.
"""


def _verdict_counts(client):
    resp = client.get("/analytics/verdict-distribution")
    assert resp.status_code == 200
    return {row["verdict"]: row["count"] for row in resp.json()}


def _create_blocked_transaction(client, sample_profile):
    # Same mule-pattern balance drain as test_pipeline's obvious-fraud case:
    # policy_agent flags it, which always wins the coordinator's decision
    # table -- deterministically "block", regardless of ML model behavior.
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
    return body


def test_verdict_distribution_reflects_new_transactions(client, sample_profile):
    before = _verdict_counts(client)
    _create_blocked_transaction(client, sample_profile)
    _create_blocked_transaction(client, sample_profile)
    after = _verdict_counts(client)

    assert after.get("block", 0) == before.get("block", 0) + 2


def test_agent_agreement_rate_shape_and_delta(client, sample_profile):
    before = client.get("/analytics/agent-agreement-rate").json()
    _create_blocked_transaction(client, sample_profile)
    after = client.get("/analytics/agent-agreement-rate").json()

    assert set(after["overall"]) == {"agree", "disagree", "total", "rate"}
    assert after["overall"]["total"] == before["overall"]["total"] + 1
    assert 0.0 <= after["overall"]["rate"] <= 1.0
    assert len(after["pairs"]) == 3
    for pair in after["pairs"]:
        assert set(pair) == {"agents", "agree", "total", "rate"}
        assert 0.0 <= pair["rate"] <= 1.0


def test_verdict_trend_groups_by_date(client, sample_profile):
    _create_blocked_transaction(client, sample_profile)
    resp = client.get("/analytics/verdict-trend")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    day = next(row for row in rows if row["date"] == "2024-06-15")
    assert day["block"] >= 1
    assert set(day) == {"date", "allow", "escalate", "block"}
