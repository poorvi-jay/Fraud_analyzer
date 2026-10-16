def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_review_unknown_user_returns_404(client):
    resp = client.post(
        "/transactions/review",
        json={
            "user_id": "nobody",
            "amount": 100.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 500.0,
            "origin_balance_after": 400.0,
            "location_country": "US",
        },
    )
    assert resp.status_code == 404


def test_review_list_and_detail_round_trip(client, sample_profile):
    review = client.post(
        "/transactions/review",
        json={
            "user_id": sample_profile.user_id,
            "amount": 150.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 1000.0,
            "origin_balance_after": 850.0,
            "location_country": "US",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )
    assert review.status_code == 200
    body = review.json()
    assert len(body["opinions"]) == 3
    assert body["review_result"]["final_verdict"] in ("allow", "escalate", "block")

    listing = client.get("/transactions")
    assert listing.status_code == 200
    assert any(item["id"] == body["id"] for item in listing.json())

    detail = client.get(f"/transactions/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == body["id"]


def test_transaction_detail_404(client):
    resp = client.get("/transactions/does-not-exist")
    assert resp.status_code == 404


def test_override_without_auth_returns_401(client, escalated_case):
    _, review_result = escalated_case
    resp = client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "approve", "note": "looks fine"},
    )
    assert resp.status_code == 401


def test_override_non_escalated_case_returns_400(client, allowed_case, mock_reviewer):
    _, review_result = allowed_case
    resp = client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "approve", "note": "n/a"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 400


def test_override_escalated_case_persists_and_is_visible_on_detail(client, escalated_case, mock_reviewer):
    txn, review_result = escalated_case
    resp = client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "reject", "note": "confirmed fraud after review"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["human_reviews"]) == 1
    assert body["human_reviews"][0]["decision"] == "reject"
    assert body["human_reviews"][0]["reviewer_id"] == "test-reviewer-id"

    detail = client.get(f"/transactions/{txn.id}")
    assert detail.status_code == 200
    human_reviews = detail.json()["review_result"]["human_reviews"]
    assert len(human_reviews) == 1
    assert human_reviews[0]["note"] == "confirmed fraud after review"


def test_simulate_with_existing_user_returns_verdict_and_persists_nothing(client, sample_profile):
    listing_before = client.get("/transactions").json()

    resp = client.post(
        "/transactions/simulate",
        json={
            "user_id": sample_profile.user_id,
            "amount": 150.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 1000.0,
            "origin_balance_after": 850.0,
            "location_country": "US",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["opinions"]) == 3
    assert body["final_verdict"] in ("allow", "escalate", "block")

    listing_after = client.get("/transactions").json()
    assert len(listing_after) == len(listing_before)


def test_simulate_with_inline_profile(client):
    resp = client.post(
        "/transactions/simulate",
        json={
            "profile": {
                "home_country": "US",
                "typical_transaction_amount": 200.0,
                "travel_frequency": "never",
                "account_age_days": 400,
            },
            "amount": 8000.0,
            "transaction_type": "TRANSFER",
            "origin_balance_before": 8000.0,
            "origin_balance_after": 0.0,
            "location_country": "FR",
            "occurred_at": "2024-06-15T10:00:00",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["final_verdict"] == "block"


def test_simulate_without_user_id_or_profile_returns_422(client):
    resp = client.post(
        "/transactions/simulate",
        json={
            "amount": 100.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 500.0,
            "origin_balance_after": 400.0,
            "location_country": "US",
        },
    )
    assert resp.status_code == 422


def test_simulate_unknown_user_returns_404(client):
    resp = client.post(
        "/transactions/simulate",
        json={
            "user_id": "nobody",
            "amount": 100.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 500.0,
            "origin_balance_after": 400.0,
            "location_country": "US",
        },
    )
    assert resp.status_code == 404


def _simulate(client, **overrides):
    payload = {
        "profile": {
            "home_country": "US",
            "typical_transaction_amount": 200.0,
            "travel_frequency": "never",
            "account_age_days": 400,
        },
        "amount": 100.0,
        "transaction_type": "PAYMENT",
        "origin_balance_before": 500.0,
        "origin_balance_after": 400.0,
        "location_country": "US",
        "occurred_at": "2024-06-15T10:00:00",
    }
    payload.update(overrides)
    return client.post("/transactions/simulate", json=payload)


def test_simulate_rejects_balance_drop_larger_than_amount(client):
    # The playground case that produced a self-contradictory SHAP explanation.
    resp = _simulate(
        client,
        amount=900_000.0,
        transaction_type="TRANSFER",
        origin_balance_before=9_500_000.0,
        origin_balance_after=0.0,
    )
    assert resp.status_code == 422
    assert "cannot move the origin balance by more than its own amount" in resp.text


def test_review_rejects_unreconciled_balances_before_looking_up_the_user(client):
    resp = client.post(
        "/transactions/review",
        json={
            "user_id": "nobody",
            "amount": 100.0,
            "transaction_type": "PAYMENT",
            "origin_balance_before": 500.0,
            "origin_balance_after": 0.0,
            "location_country": "US",
        },
    )
    assert resp.status_code == 422


def test_simulate_checks_cash_in_increase_against_amount(client):
    assert _simulate(
        client, transaction_type="CASH_IN", origin_balance_before=500.0, origin_balance_after=600.0
    ).status_code == 200
    assert _simulate(
        client, transaction_type="CASH_IN", origin_balance_before=500.0, origin_balance_after=900.0
    ).status_code == 422


def test_simulate_tolerates_a_cent_of_float_noise(client):
    assert _simulate(client, origin_balance_after=399.995).status_code == 200
    assert _simulate(client, origin_balance_after=399.98).status_code == 422


def test_simulate_allows_balance_moving_less_than_amount(client):
    # PaySim is full of these (zero-balance accounts, debits clamped at 0);
    # the check is one-sided so they are not rejected.
    assert _simulate(client, origin_balance_before=0.0, origin_balance_after=0.0).status_code == 200
    assert _simulate(client, origin_balance_before=60.0, origin_balance_after=0.0).status_code == 200


def test_example_users_returns_list(client, sample_profile):
    resp = client.get("/transactions/example-users")
    assert resp.status_code == 200
    body = resp.json()
    assert any(u["user_id"] == sample_profile.user_id for u in body)


def test_override_unknown_review_result_returns_404(client, mock_reviewer):
    resp = client.post(
        "/reviews/does-not-exist/override",
        json={"decision": "approve", "note": "n/a"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 404
