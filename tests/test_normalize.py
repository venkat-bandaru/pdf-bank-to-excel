"""Tests for normalize.reconcile — the statement-level accountant's check.

These exercise the reconciliation in isolation with hand-built transactions
and summaries, so they are deterministic and need no PDF fixture.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from statement_to_excel.models import RawSummary, Transaction
from statement_to_excel.normalize import reconcile

_PDF = Path("synthetic.pdf")


def _out(amount: str, balance: str | None = None) -> Transaction:
    return Transaction(
        date=datetime.date(2025, 2, 10),
        description="payment",
        money_out=Decimal(amount),
        money_in=None,
        balance=Decimal(balance) if balance is not None else None,
        confidence="ok",
    )


def _in(amount: str, balance: str | None = None) -> Transaction:
    return Transaction(
        date=datetime.date(2025, 2, 10),
        description="receipt",
        money_out=None,
        money_in=Decimal(amount),
        balance=Decimal(balance) if balance is not None else None,
        confidence="ok",
    )


def test_reconcile_passes_when_totals_agree() -> None:
    txns = [_in("100.00"), _out("30.00"), _out("20.00")]
    summary = RawSummary(
        opening_balance="200.00",
        paid_in="100.00",
        paid_out="50.00",
        closing_balance="250.00",
    )
    rec = reconcile(txns, summary, _PDF)
    assert rec.ok
    assert rec.issues == ()
    assert rec.extracted_paid_in == Decimal("100.00")
    assert rec.extracted_paid_out == Decimal("50.00")


def test_reconcile_flags_a_missing_payment_out() -> None:
    """Dropping one outbound row must surface as a Payments Out mismatch and
    a closing-balance mismatch — the failure the per-row chain check missed."""
    # Statement says 50.00 went out, but only one 30.00 row was extracted.
    txns = [_in("100.00"), _out("30.00")]
    summary = RawSummary(
        opening_balance="200.00",
        paid_in="100.00",
        paid_out="50.00",
        closing_balance="250.00",
    )
    rec = reconcile(txns, summary, _PDF)
    assert not rec.ok
    joined = " ".join(rec.issues)
    assert "Payments Out" in joined
    assert "Closing balance" in joined


def test_reconcile_tolerates_one_penny_rounding() -> None:
    txns = [_out("50.00")]
    summary = RawSummary(
        opening_balance="200.00",
        paid_in="0.00",
        paid_out="50.01",
        closing_balance="149.99",
    )
    rec = reconcile(txns, summary, _PDF)
    assert rec.ok


def test_reconcile_skips_checks_for_absent_figures() -> None:
    """Empty summary strings mean that check is skipped, not failed."""
    txns = [_out("50.00")]
    summary = RawSummary(
        opening_balance="", paid_in="", paid_out="", closing_balance=""
    )
    rec = reconcile(txns, summary, _PDF)
    assert rec.ok
    assert rec.stated_paid_out is None
