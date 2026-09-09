"""Single-case PDF export (PRD 7.3).

Asserting on rendered PDF layout is brittle and not worth it; what these
check is that the endpoint produces a real, non-trivial PDF for every case
shape the queue can contain -- including the awkward ones (no verdict yet,
overrides attached, unicode in a reviewer's note) -- and that the demo-mode
provenance notice is actually in the bytes.
"""

from app.reporting import build_case_report


def _extract_text(pdf: bytes) -> str:
    """Rough text recovery from the PDF's content streams.

    ReportLab writes page content ASCII85-encoded over Flate, so a substring
    search on the raw bytes finds nothing. This undoes both layers and
    concatenates the literal strings passed to the text-showing operator,
    which is enough to assert that a given phrase reached the page.
    """
    import base64
    import re
    import zlib

    chunks: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.DOTALL):
        raw = match.group(1).strip()
        try:
            raw = base64.a85decode(raw, adobe=True)
        except ValueError:
            pass  # stream wasn't ASCII85 (uncompressed build, or a font blob)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        # PDF string literals escape their own parens as \( \), so the
        # pattern has to skip escaped chars rather than stop at the first ")".
        chunks.extend(
            m.group(1) for m in re.finditer(rb"\(((?:\\.|[^\\()])*)\)\s*Tj", raw, re.DOTALL)
        )
    text = b" ".join(chunks)
    for escaped, literal in ((rb"\(", b"("), (rb"\)", b")"), (rb"\\", b"\\")):
        text = text.replace(escaped, literal)
    return text.decode("latin-1", errors="replace")


def _create_case(client, sample_profile) -> dict:
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
    return resp.json()


def test_report_endpoint_returns_a_pdf(client, sample_profile):
    case = _create_case(client, sample_profile)
    resp = client.get(f"/transactions/{case['id']}/report.pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert f'filename="case-{case["id"][:8]}.pdf"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")
    assert len(resp.content) > 1000


def test_report_contains_the_verdict_and_the_demo_mode_notice(client, sample_profile):
    case = _create_case(client, sample_profile)
    text = _extract_text(client.get(f"/transactions/{case['id']}/report.pdf").content)

    assert "BLOCK" in text
    # A PDF outlives the page it came from; the provenance has to travel with it.
    assert "DEMO MODE" in text
    assert "Anomaly (ML)" in text
    assert "Policy (rules)" in text


def test_report_for_an_unknown_case_is_404(client):
    resp = client.get("/transactions/does-not-exist/report.pdf")
    assert resp.status_code == 404


def test_report_renders_a_case_with_no_verdict_yet(db_session, sample_profile):
    """A Transaction row can exist without a ReviewResult (a pipeline run
    that failed after insert). The export must still render rather than
    500 on a null verdict.
    """
    from datetime import datetime

    from app.models import Transaction

    txn = Transaction(
        user_id=sample_profile.user_id,
        amount=42.0,
        transaction_type="PAYMENT",
        origin_balance_before=100.0,
        origin_balance_after=58.0,
        location_country="US",
        occurred_at=datetime(2024, 6, 15, 10, 0, 0),
    )
    db_session.add(txn)
    db_session.commit()
    db_session.refresh(txn)

    pdf = build_case_report(txn)
    assert pdf.startswith(b"%PDF-")
    assert "no verdict recorded".upper() in _extract_text(pdf).upper()


def test_report_includes_reviewer_overrides(client, escalated_case, mock_reviewer):
    txn, review_result = escalated_case
    client.post(
        f"/reviews/{review_result.id}/override",
        json={"decision": "reject", "note": "Confirmed with the customer -- card was cloned."},
    )

    text = _extract_text(client.get(f"/transactions/{txn.id}/report.pdf").content)
    assert "reject" in text
    assert "cloned" in text
    assert "test-reviewer-id" in text
