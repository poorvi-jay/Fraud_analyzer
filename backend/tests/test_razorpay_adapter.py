"""Razorpay ingestion tests.

The headline test is test_full_pipeline_from_razorpay_payload: a realistic
test-mode Payment object goes in, all four agents produce opinions, and the
coordinator produces a verdict. The rest pin down the honesty properties --
that missing balances are refused rather than defaulted, and that degraded
mode is visible in the response rather than silent.
"""
import pytest

from app.adapters import razorpay_adapter


def razorpay_test_payment(**overrides) -> dict:
    """A realistic Razorpay test-mode Payment entity (GET /v1/payments/:id).

    Field names and shapes follow Razorpay's documented Payment object;
    `amount` is in paise, `created_at` is a UNIX timestamp in seconds.
    """
    payment = {
        "id": "pay_29QQoUBi66xm2f",
        "entity": "payment",
        "amount": 100000,  # paise -> 1000.00 INR
        "currency": "INR",
        "status": "captured",
        "order_id": "order_GjCr5oKh4AVC51",
        "invoice_id": None,
        "international": False,
        "method": "card",
        "amount_refunded": 0,
        "refund_status": None,
        "captured": True,
        "description": "Payment for Adventure Bag",
        "card_id": "card_KOdY30ajbuyOYN",
        "bank": None,
        "wallet": None,
        "vpa": None,
        "email": "gaurav.kumar@example.com",
        "contact": "9000090000",
        "customer_id": "cust_DitrYCFtCIokBO",
        "token_id": "token_KOdY$DBYQOv08n",
        "notes": {"merchant_order_id": "shirt_0001"},
        "fee": 2360,
        "tax": 360,
        "error_code": None,
        "error_description": None,
        "error_source": None,
        "error_step": None,
        "error_reason": None,
        "acquirer_data": {"auth_code": "776324"},
        "created_at": 1718000000,
    }
    payment.update(overrides)
    return payment


LEDGER_BALANCES = {"origin_balance_before": 5000.0, "origin_balance_after": 4000.0}


# --- adapter unit behavior ---


def test_amount_converts_from_paise_to_rupees():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["amount"] == 1000.00


def test_zero_decimal_currency_is_not_divided():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(amount=5000, currency="JPY"), home_country="JP",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["amount"] == 5000.0


def test_unknown_currency_assumes_two_decimals_and_says_so():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(currency="XYZ"), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert any("not in the adapter's minor-unit table" in w for w in adapted.warnings)


def test_created_at_unix_seconds_becomes_occurred_at():
    from datetime import datetime

    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["occurred_at"] == datetime(2024, 6, 10, 6, 13, 20)


def test_domestic_payment_is_not_foreign_to_the_profile():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(international=False), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["location_country"] == "IN"


def test_international_payment_uses_a_sentinel_not_an_invented_country():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(international=True), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["location_country"] == razorpay_adapter.FOREIGN_SENTINEL_COUNTRY
    assert any("sentinel" in w for w in adapted.warnings)


def test_customer_id_is_preferred_identity():
    user_id, warning = razorpay_adapter.resolve_user_id(razorpay_test_payment())
    assert user_id == "cust_DitrYCFtCIokBO"
    assert warning is None


def test_missing_customer_id_falls_back_to_a_hash_not_raw_pii():
    payment = razorpay_test_payment(customer_id=None)
    user_id, warning = razorpay_adapter.resolve_user_id(payment)
    assert user_id.startswith("rzp_anon_")
    assert payment["email"] not in user_id
    assert warning is not None


def test_payment_with_no_identity_at_all_is_rejected():
    payment = razorpay_test_payment(customer_id=None, email=None, contact=None)
    with pytest.raises(razorpay_adapter.RazorpayAdapterError):
        razorpay_adapter.resolve_user_id(payment)


def test_balances_are_left_unset_not_defaulted_when_absent():
    """The core honesty property: no fabricated balance ever reaches the
    model. Defaulting to 0 puts the model off-manifold; defaulting to
    `amount` reproduces PaySim's exact fraud signature."""
    adapted = razorpay_adapter.adapt_payment(razorpay_test_payment(), home_country="IN")

    assert adapted.transaction["origin_balance_before"] is None
    assert adapted.transaction["origin_balance_after"] is None
    assert adapted.anomaly_scorable is False
    assert adapted.feature_availability["balance_features"] is False
    assert any("NO BALANCE CONTEXT SUPPLIED" in w for w in adapted.warnings)


def test_transaction_type_collapse_is_flagged_as_a_lost_signal():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(method="upi"), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert adapted.transaction["transaction_type"] == "PAYMENT"
    assert adapted.transaction["source_method"] == "upi"
    assert adapted.feature_availability["transaction_type_signal"] is False


def test_failed_payment_is_flagged_as_not_settled():
    adapted = razorpay_adapter.adapt_payment(
        razorpay_test_payment(status="failed", captured=False), home_country="IN",
        balance_context=razorpay_adapter.BalanceContext(**LEDGER_BALANCES),
    )
    assert any("attempt rather than settled" in w for w in adapted.warnings)


# --- full pipeline through the endpoint ---


@pytest.fixture
def razorpay_profile(db_session):
    """A user_profiles row keyed by the Razorpay customer_id, standing in for
    the spend history Razorpay's API does not serve."""
    from datetime import date

    from app.models import UserProfile

    user_id = "cust_DitrYCFtCIokBO"
    existing = db_session.get(UserProfile, user_id)
    if existing is not None:
        return existing
    profile = UserProfile(
        user_id=user_id,
        account_created=date(2023, 1, 1),
        home_country="IN",
        typical_transaction_amount=900.0,
        travel_frequency="rare",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return profile


def test_full_pipeline_from_razorpay_payload(client, razorpay_profile):
    """A realistic Razorpay test-mode payment produces all four agent
    outputs and a coordinator verdict."""
    resp = client.post(
        "/transactions/razorpay/simulate",
        json={"payment": razorpay_test_payment(), "balance_context": LEDGER_BALANCES},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    agents = {o["agent_name"] for o in body["opinions"]}
    assert agents == {"anomaly_agent", "context_agent", "policy_agent"}
    for opinion in body["opinions"]:
        assert 0.0 <= opinion["score"] <= 1.0
        assert isinstance(opinion["flag"], bool)
        assert opinion["reasoning"]

    # The coordinator is the fourth agent: it emits the verdict, not an opinion row.
    assert body["final_verdict"] in ("allow", "escalate", "block")
    assert body["coordinator_reasoning"]

    assert body["user_id"] == "cust_DitrYCFtCIokBO"
    assert body["anomaly_scored"] is True
    assert body["feature_availability"]["balance_features"] is True


def test_missing_balances_are_refused_by_default(client, razorpay_profile):
    resp = client.post(
        "/transactions/razorpay/simulate", json={"payment": razorpay_test_payment()}
    )
    assert resp.status_code == 422
    assert "balance_context" in resp.json()["detail"]


def test_degraded_mode_runs_but_declares_the_anomaly_model_unscored(client, razorpay_profile):
    resp = client.post(
        "/transactions/razorpay/simulate",
        json={"payment": razorpay_test_payment(), "allow_degraded": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["anomaly_scored"] is False
    assert body["final_verdict"] in ("allow", "escalate", "block")

    anomaly = next(o for o in body["opinions"] if o["agent_name"] == "anomaly_agent")
    assert "NOT SCORED" in anomaly["reasoning"]
    # score 0.0 must not be readable as "low risk"
    assert "not 'low risk'" in anomaly["reasoning"]
    assert any("NO BALANCE CONTEXT" in w for w in body["adapter_warnings"])


def test_inline_profile_works_without_a_seeded_customer(client):
    resp = client.post(
        "/transactions/razorpay/simulate",
        json={
            "payment": razorpay_test_payment(customer_id="cust_NEVER_SEEDED"),
            "balance_context": LEDGER_BALANCES,
            "profile": {
                "home_country": "IN",
                "typical_transaction_amount": 900.0,
                "travel_frequency": "rare",
                "account_age_days": 400,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == "cust_NEVER_SEEDED"


def test_unknown_customer_without_inline_profile_is_404(client):
    resp = client.post(
        "/transactions/razorpay/simulate",
        json={
            "payment": razorpay_test_payment(customer_id="cust_NO_PROFILE_ANYWHERE"),
            "balance_context": LEDGER_BALANCES,
        },
    )
    assert resp.status_code == 404
    assert "spend history" in resp.json()["detail"]


def test_razorpay_path_never_writes_to_the_database(client, razorpay_profile, db_session):
    from app.models import Transaction

    before = db_session.query(Transaction).count()
    client.post(
        "/transactions/razorpay/simulate",
        json={"payment": razorpay_test_payment(), "balance_context": LEDGER_BALANCES},
    )
    db_session.expire_all()
    assert db_session.query(Transaction).count() == before


def test_policy_safe_view_refuses_a_type_the_mule_rule_could_match():
    """The zero balances in policy_safe_view are only defensible while the
    mule-drain rule is gated out by transaction_type. Pin that."""
    txn = razorpay_adapter.adapt_payment(razorpay_test_payment(), home_country="IN").transaction

    assert razorpay_adapter.policy_safe_view(txn)["origin_balance_before"] == 0.0

    with pytest.raises(AssertionError):
        razorpay_adapter.policy_safe_view({**txn, "transaction_type": "TRANSFER"})


def test_degraded_mode_still_applies_the_balance_free_policy_rules(client, razorpay_profile):
    """New-account + reporting-threshold rules read only amount and account
    age, so they must survive degraded mode."""
    resp = client.post(
        "/transactions/razorpay/simulate",
        json={
            "payment": razorpay_test_payment(amount=99999999999),
            "allow_degraded": True,
            "profile": {
                "home_country": "IN",
                "typical_transaction_amount": 900.0,
                "travel_frequency": "rare",
                "account_age_days": 0,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    policy = next(o for o in body["opinions"] if o["agent_name"] == "policy_agent")
    assert policy["flag"] is True
    assert "account is only 0 day(s) old" in policy["reasoning"]
    assert body["final_verdict"] == "block"
