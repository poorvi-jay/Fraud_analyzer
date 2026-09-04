"""
Verify backend/app/adapters/razorpay_adapter.py against REAL Razorpay
test-mode API responses, rather than against Razorpay's documentation.

Why this exists: the adapter's field mapping was written from Razorpay's
published Payment entity schema. Documentation and reality drift -- fields
get added, some are absent on certain payment methods, and some are only
populated in flows the docs don't emphasise. This script fetches actual
payments from YOUR test-mode account and reports, field by field, whether
what the adapter assumes is what the API sends.

It never writes anything: one authenticated GET against /v1/payments.

CREDENTIALS
-----------
Read from the environment. Nothing is prompted for, and no key is written
to the report.

    export RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
    export RAZORPAY_KEY_SECRET=xxxxxxxx
    python scripts/verify_razorpay_schema.py

A repo-root or backend/.env file is also read, if present.

LIVE KEYS ARE REFUSED. A live key would pull real customers' email
addresses and phone numbers into a local file. This script only accepts
rzp_test_ keys, deliberately and with no override flag.

PRIVACY
-------
The report records only whether identity fields are PRESENT, never their
values. Even in test mode those fields often hold real personal data typed
in during testing, and the report is meant to be shareable.
"""
import argparse
import base64
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.adapters import razorpay_adapter  # noqa: E402

RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"

# Every field the adapter actually reads. This list is the point of the
# script: if any of these is missing or a different type in real responses,
# the mapping is built on a false assumption.
ADAPTER_READS = [
    ("amount", int, "required -- amount in paise"),
    ("currency", str, "minor-unit conversion"),
    ("entity", str, "sanity check that this is a payment object"),
    ("id", str, "provenance only"),
    ("status", str, "settled-vs-attempt warning"),
    ("method", str, "recorded as metadata; NOT mapped to transaction_type"),
    ("international", bool, "the only geography signal -> is_foreign"),
    ("customer_id", str, "preferred identity"),
    ("email", str, "identity fallback (hashed)"),
    ("contact", str, "identity fallback (hashed)"),
    ("created_at", int, "-> occurred_at"),
    ("amount_refunded", int, "partial-refund warning"),
]
IDENTITY_FIELDS = {"email", "contact"}


def load_env_files() -> None:
    """Minimal .env reader. Deliberately not python-dotenv: that dependency
    is not in requirements.txt on this branch, and this is a one-off script.
    Never overrides an already-set variable."""
    for path in (ROOT / ".env", ROOT / "backend" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            os.environ.setdefault(key, value)


def fetch_payments(key_id: str, key_secret: str, count: int) -> list[dict]:
    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    resp = httpx.get(
        RAZORPAY_PAYMENTS_URL,
        params={"count": min(count, 100)},
        headers={"Authorization": f"Basic {token}"},
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise SystemExit(
            "401 Unauthorized -- Razorpay rejected the key pair. Check RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET are the TEST-mode pair from Dashboard -> Account & Settings -> "
            "API Keys (test mode has its own separate pair)."
        )
    resp.raise_for_status()
    return resp.json().get("items", [])


def describe_field(payments: list[dict], name: str, expected_type: type) -> dict:
    present = [p for p in payments if p.get(name) is not None]
    types = Counter(type(p[name]).__name__ for p in present)
    # bool is a subclass of int in Python; check it explicitly so an int
    # arriving where a bool is expected is not silently accepted.
    def ok(v):
        if expected_type is bool:
            return isinstance(v, bool)
        if expected_type is int:
            return isinstance(v, int) and not isinstance(v, bool)
        return isinstance(v, expected_type)

    mismatched = [p[name] for p in present if not ok(p[name])]
    return {
        "present": len(present),
        "absent_or_null": len(payments) - len(present),
        "types": dict(types),
        "type_mismatches": len(mismatched),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=100, help="Payments to fetch (max 100).")
    parser.add_argument("--output", default=str(ROOT / "docs" / "razorpay_schema_verification.md"))
    args = parser.parse_args()

    load_env_files()
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

    if not key_id or not key_secret:
        raise SystemExit(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set.\n"
            "Get the TEST-mode pair from the Razorpay Dashboard (toggle to Test Mode) ->\n"
            "Account & Settings -> API Keys, then:\n"
            "  export RAZORPAY_KEY_ID=rzp_test_...\n"
            "  export RAZORPAY_KEY_SECRET=...\n"
        )
    if not key_id.startswith("rzp_test_"):
        raise SystemExit(
            f"Refusing to run: RAZORPAY_KEY_ID starts with {key_id.split('_')[1] if '_' in key_id else key_id!r}, "
            "not 'rzp_test_'. A live key would pull real customers' email addresses and phone "
            "numbers into a local report file. Use test-mode credentials. There is no override."
        )

    print(f"Fetching up to {args.count} test-mode payments...")
    payments = fetch_payments(key_id, key_secret, args.count)
    print(f"Got {len(payments)}.")

    if not payments:
        raise SystemExit(
            "\nYour test-mode account has no payments yet, so there is nothing to verify.\n"
            "Create one first: Razorpay Dashboard in Test Mode -> Payment Links (or Payment\n"
            "Pages), open the link, and pay with test card 4111 1111 1111 1111, any future\n"
            "expiry, any CVV. Then re-run this script.\n"
        )

    # --- field-by-field comparison ---
    field_report = {name: describe_field(payments, name, t) for name, t, _ in ADAPTER_READS}

    # --- run the real payments through the adapter ---
    adapter_failures = []
    warning_counts = Counter()
    availability_counts = defaultdict(Counter)
    for p in payments:
        try:
            adapted = razorpay_adapter.adapt_payment(
                p,
                home_country="IN",
                balance_context=razorpay_adapter.BalanceContext(
                    origin_balance_before=10000.0, origin_balance_after=9000.0
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- any failure is a finding
            adapter_failures.append({"payment_id": p.get("id"), "error": f"{type(exc).__name__}: {exc}"})
            continue
        for w in adapted.warnings:
            warning_counts[w.split(".")[0][:90]] += 1
        for k, v in adapted.feature_availability.items():
            availability_counts[k][v] += 1

    observed = {
        "status": Counter(p.get("status") for p in payments),
        "method": Counter(p.get("method") for p in payments),
        "currency": Counter(p.get("currency") for p in payments),
    }

    # --- unexpected fields (documentation drift in the other direction) ---
    known = {n for n, _, _ in ADAPTER_READS} | {
        "order_id", "invoice_id", "captured", "description", "card_id", "card", "bank",
        "wallet", "vpa", "upi", "token_id", "notes", "fee", "tax", "refund_status",
        "error_code", "error_description", "error_source", "error_step", "error_reason",
        "acquirer_data",
    }
    unexpected = sorted({k for p in payments for k in p} - known)

    # --- report ---
    lines = [
        "# Razorpay schema verification",
        "",
        f"Ran `scripts/verify_razorpay_schema.py` against **{len(payments)} real test-mode "
        "payments**, to check the adapter's mapping against actual API responses rather than "
        "against Razorpay's published schema.",
        "",
        "No identity values are recorded below -- only whether the field was present.",
        "",
        "## Fields the adapter reads",
        "",
        "| Field | Used for | Present | Absent/null | Types seen | Type mismatches |",
        "|---|---|---|---|---|---|",
    ]
    for name, _, purpose in ADAPTER_READS:
        r = field_report[name]
        # ASCII only: this string is printed to the console, and a Windows
        # cp1252 terminal raises UnicodeEncodeError on non-ASCII.
        flag = " <-- MISMATCH" if r["type_mismatches"] else ""
        lines.append(
            f"| `{name}` | {purpose} | {r['present']}/{len(payments)} | {r['absent_or_null']} | "
            f"{', '.join(r['types']) or '--'} | {r['type_mismatches']}{flag} |"
        )

    lines += ["", "## Values actually observed", ""]
    for key, counter in observed.items():
        pretty = ", ".join(f"`{k}` x{v}" for k, v in counter.most_common())
        lines.append(f"- **{key}**: {pretty}")

    lines += ["", "## Adapter run over the real payments", ""]
    if adapter_failures:
        lines.append(f"**{len(adapter_failures)} of {len(payments)} payments FAILED to adapt:**")
        lines.append("")
        for f in adapter_failures[:20]:
            lines.append(f"- `{f['payment_id']}` -- {f['error']}")
    else:
        lines.append(f"All {len(payments)} payments adapted without error.")

    lines += ["", "### Feature availability across the sample", ""]
    for feature, counter in availability_counts.items():
        lines.append(f"- `{feature}`: available x{counter[True]}, unavailable x{counter[False]}")

    lines += ["", "### Warnings raised (deduplicated)", ""]
    if warning_counts:
        for w, n in warning_counts.most_common():
            lines.append(f"- x{n} -- {w}")
    else:
        lines.append("None.")

    lines += ["", "## Fields present in real responses the adapter does not know about", ""]
    lines.append(", ".join(f"`{u}`" for u in unexpected) if unexpected else "None.")

    lines += [
        "",
        "## How to read this",
        "",
        "- Any **type mismatch** means the adapter's assumption is wrong and the mapping needs a fix.",
        "- A field with a high **absent/null** count that the adapter treats as reliable is a gap "
        "worth documenting, especially `customer_id` (identity) and `international` (the only "
        "geography signal).",
        "- **Unexpected fields** are informational; the adapter ignores unknown keys by design.",
        "",
    ]

    report = "\n".join(lines)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report, encoding="utf-8")

    print()
    print(report)
    print(f"\nWrote {args.output}")

    # Non-zero exit if anything contradicts the adapter, so this is usable in CI.
    problems = sum(r["type_mismatches"] for r in field_report.values()) + len(adapter_failures)
    if problems:
        print(f"\n{problems} finding(s) contradict the adapter's assumptions -- see the table above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
