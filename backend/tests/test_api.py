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


def test_override_unknown_review_result_returns_404(client, mock_reviewer):
    resp = client.post(
        "/reviews/does-not-exist/override",
        json={"decision": "approve", "note": "n/a"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 404
