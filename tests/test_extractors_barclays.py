"""Tests for the Barclays PDF statement extractor.

Sample under test: ``tests/samples/Statement 21-FEB-25.pdf`` — a six-page
Barclays Bank UK business current account statement covering 22 January
to 21 February 2025. The printed "At a glance" block on page 2 gives the
ground-truth Start / End balance and Money In / Money Out totals that
the tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.barclays import BarclaysExtractor
from statement_to_excel.models import RawRow

# Hand-counted from tests/samples/Statement 21-FEB-25.pdf.
_EXPECTED_ROW_COUNT = 45
# Printed At-a-glance block on page 2 of the sample.
_OPENING_BALANCE = Decimal("195.76")
_CLOSING_BALANCE = Decimal("1482.31")
_TOTAL_PAID_IN = Decimal("13468.56")
_TOTAL_PAID_OUT = Decimal("12182.01")
_FIRST_DATE = datetime.date(2025, 1, 22)
_LAST_DATE = datetime.date(2025, 2, 19)


@pytest.fixture
def feb_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Barclays extractor on Statement 21-FEB-25.pdf once per test."""
    return BarclaysExtractor().extract(samples_dir / "Statement 21-FEB-25.pdf")


def test_barclays_feb_row_count(feb_rows: list[RawRow]) -> None:
    assert len(feb_rows) == _EXPECTED_ROW_COUNT


def test_barclays_feb_first_row_is_newest(feb_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is 19 Feb's only txn."""
    first = feb_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description.startswith("Direct Debit to Ems")
    # The multi-line continuation tokens must survive into the description.
    assert "AL02071842Msc0125" in first.description
    assert "This Is A New Direct Debit Payment" in first.description
    assert first.money_out == "3.69"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_barclays_feb_last_row_is_oldest(feb_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = feb_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description.startswith("Direct Debit to BCard Commercial")
    assert "5476761010445246" in last.description
    assert last.money_out == "14.99"
    assert last.money_in == ""
    assert last.balance == "180.77"


def test_barclays_feb_totals_match_at_a_glance(feb_rows: list[RawRow]) -> None:
    """Sums of the paid-in / paid-out columns equal the printed totals."""
    total_in = sum(
        (Decimal(r.money_in) for r in feb_rows if r.money_in), Decimal("0")
    )
    total_out = sum(
        (Decimal(r.money_out) for r in feb_rows if r.money_out), Decimal("0")
    )
    assert total_in == _TOTAL_PAID_IN
    assert total_out == _TOTAL_PAID_OUT


def test_barclays_feb_exactly_one_direction_per_row(
    feb_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in (never both, never neither)."""
    for r in feb_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_barclays_feb_dates_within_period(feb_rows: list[RawRow]) -> None:
    for r in feb_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_barclays_feb_direct_credits_classified_as_money_in(
    feb_rows: list[RawRow],
) -> None:
    """Every "Direct Credit From..." row must land in money_in.

    The balance-differential check decides direction; a regression that
    inverted the sign would surface here as Direct Credit rows leaking
    into money_out before it surfaced as a balance-chain failure.
    """
    direct_credits = [
        r for r in feb_rows if r.description.startswith("Direct Credit")
    ]
    assert direct_credits, "expected at least one Direct Credit row"
    for r in direct_credits:
        assert r.money_in and not r.money_out, (
            f"Direct Credit miscategorised as money_out: {r}"
        )


def test_barclays_feb_multiline_description_assembled(
    feb_rows: list[RawRow],
) -> None:
    """A description split across three lines must be folded into one field.

    "Direct Debit to Takepayments Ltd" prints as
    ``Direct Debit to Takepayments Ltd / Ref: Tpay363519 0052960 / This Is
    A New Direct Debit Payment / 30.00 82.43`` on 28 Jan. All three
    description fragments should land in the description field.
    """
    matching = [
        r for r in feb_rows if "Tpay363519 0052960" in r.description
    ]
    assert len(matching) == 1
    r = matching[0]
    assert r.description.startswith("Direct Debit to Takepayments Ltd")
    assert "This Is A New Direct Debit Payment" in r.description
    assert r.money_out == "30.00"


def test_barclays_feb_inline_date_in_description_kept(
    feb_rows: list[RawRow],
) -> None:
    """A "DD MMM" fragment that closes a multi-line description must not be
    mis-parsed as a new date. The 14 Feb "Card Payment to Google One On /
    12 Feb / 1.59 1,989.52" row would otherwise produce a 12 Feb txn with
    an empty description and a 14 Feb txn missing its body."""
    matching = [
        r for r in feb_rows
        if r.description.startswith("Card Payment to Google One")
    ]
    assert len(matching) == 1
    r = matching[0]
    assert r.date == "2025-02-14"
    assert "12 Feb" in r.description
    assert r.money_out == "1.59"


def test_barclays_feb_no_chrome_leaks_into_rows(feb_rows: list[RawRow]) -> None:
    """Per-page chrome (footer disclaimers, page numbers, the
    "Balance brought forward" / "Total Payments/Receipts" markers, and the
    "Start Balance" opening line) must not pollute any field."""
    for r in feb_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "Continued" not in value
            assert "brought forward" not in value.lower()
            assert "carried forward" not in value.lower()
            assert "Total Payments" not in value
            assert "Start Balance" not in value


def test_barclays_feb_balance_chain_reconstructs_closing(
    feb_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the opening balance reproduces
    the printed closing balance, and every row's printed balance agrees with
    the running total at that point."""
    running = _OPENING_BALANCE
    for r in reversed(feb_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE
