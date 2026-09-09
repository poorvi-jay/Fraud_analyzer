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


def test_override_outcomes_shape_before_any_review(client):
    body = client.get("/analytics/override-outcomes").json()
    assert set(body) == {
        "escalated_total",
        "reviewed",
        "pending",
        "review_rate",
        "total_overrides",
        "decisions",
        "approve_rate",
    }
    assert body["escalated_total"] == body["reviewed"] + body["pending"]
    assert 0.0 <= body["review_rate"] <= 1.0


def test_override_outcomes_counts_a_reviewed_case(client, escalated_case, mock_reviewer):
    _, review_result = escalated_case
    before = client.get("/analytics/override-outcomes").json()

    resp = client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "approve", "note": "Traveller, verified by phone."},
    )
    assert resp.status_code == 200

    after = client.get("/analytics/override-outcomes").json()
    assert after["reviewed"] == before["reviewed"] + 1
    assert after["decisions"]["approve"] == before["decisions"]["approve"] + 1
    assert after["pending"] == before["pending"] - 1


def test_re_reviewing_a_case_replaces_its_standing_decision(client, escalated_case, mock_reviewer):
    """A case reviewed twice is still one reviewed case, and the later
    decision is the one that counts -- otherwise a corrected review would be
    double-counted and skew the approve rate.
    """
    _, review_result = escalated_case
    before = client.get("/analytics/override-outcomes").json()

    client.post(
        f"/reviews/{review_result.id}/override", json={"decision": "approve", "note": "looks fine"}
    )
    client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "reject", "note": "second look -- account is a mule"},
    )

    after = client.get("/analytics/override-outcomes").json()
    assert after["reviewed"] == before["reviewed"] + 1
    assert after["total_overrides"] == before["total_overrides"] + 2
    assert after["decisions"]["reject"] == before["decisions"]["reject"] + 1
    assert after["decisions"]["approve"] == before["decisions"]["approve"]


def test_agent_flag_trend_reports_a_rate_per_agent_per_day(client, sample_profile):
    _create_blocked_transaction(client, sample_profile)
    resp = client.get("/analytics/agent-flag-trend")
    assert resp.status_code == 200

    day = next(row for row in resp.json() if row["date"] == "2024-06-15")
    assert set(day) == {"date", "anomaly_agent", "context_agent", "policy_agent"}
    for agent in ("anomaly_agent", "context_agent", "policy_agent"):
        assert 0.0 <= day[agent] <= 1.0
    # The seeded case is a policy-flagged mule pattern, so policy_agent's
    # flag rate for that day cannot be zero.
    assert day["policy_agent"] > 0.0


def test_verdict_trend_groups_by_date(client, sample_profile):
    _create_blocked_transaction(client, sample_profile)
    resp = client.get("/analytics/verdict-trend")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    day = next(row for row in rows if row["date"] == "2024-06-15")
    assert day["block"] >= 1
    assert set(day) == {"date", "allow", "escalate", "block"}
