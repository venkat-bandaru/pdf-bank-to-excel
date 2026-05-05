"""Tests for the Monzo Bank PDF statement extractor.

Sample under test: ``tests/samples/FEB-APR-25.pdf`` — a seven-page
Monzo Bank UK business account statement covering 01/11/2025 through
31/01/2026 (the file name is the supplier's, not the statement's).
The transaction table spans pages 1-6; page 7 is the FSCS information
page (no transactions) and page 8 is blank. The printed account
summary on page 1 gives the ground-truth totals the tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.monzobank import MonzoBankExtractor
from statement_to_excel.models import RawRow

# Hand-counted from tests/samples/FEB-APR-25.pdf (sum of transaction
# rows across pages 1-6).
_EXPECTED_ROW_COUNT = 81
# Printed "Account balance" / "Total deposits" / "Total outgoings"
# block on page 1.
_CLOSING_BALANCE = Decimal("1501.09")
_TOTAL_DEPOSITS = Decimal("10667.50")
_TOTAL_OUTGOINGS = Decimal("9450.67")
# Implied opening balance: closing - net flow.
_OPENING_BALANCE = _CLOSING_BALANCE - (_TOTAL_DEPOSITS - _TOTAL_OUTGOINGS)
_FIRST_DATE = datetime.date(2025, 11, 3)
_LAST_DATE = datetime.date(2026, 1, 30)


@pytest.fixture
def feb_apr_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Monzo extractor on FEB-APR-25.pdf once per test."""
    return MonzoBankExtractor().extract(samples_dir / "FEB-APR-25.pdf")


def test_monzobank_row_count(feb_apr_rows: list[RawRow]) -> None:
    assert len(feb_apr_rows) == _EXPECTED_ROW_COUNT


def test_monzobank_first_row_is_newest(feb_apr_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is 30/01/2026."""
    first = feb_apr_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == (
        "Saradhi kanumuri (Faster Payments) Reference: 41 watkin refurbis"
    )
    assert first.money_out == "1000.00"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_monzobank_last_row_is_oldest(feb_apr_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = feb_apr_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == (
        "PAYPROP CLIENT ACC (Faster Payments) Reference: CHEYNEY ROAD 25"
    )
    assert last.money_out == ""
    assert last.money_in == "1075.11"
    assert last.balance == "1359.37"


def test_monzobank_totals_match_account_summary(
    feb_apr_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the printed totals on page 1."""
    total_in = sum(
        (Decimal(r.money_in) for r in feb_apr_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in feb_apr_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_DEPOSITS
    assert total_out == _TOTAL_OUTGOINGS


def test_monzobank_exactly_one_direction_per_row(
    feb_apr_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in feb_apr_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_monzobank_dates_within_period(feb_apr_rows: list[RawRow]) -> None:
    for r in feb_apr_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_monzobank_multiline_description_above_and_below(
    feb_apr_rows: list[RawRow],
) -> None:
    """A description that wraps to lines above AND below the date row
    must fold all three pieces into one description field.

    The 29/01/2026 E.ON Direct Debit prints with ``E.ON NEXT LTD
    (Direct Debit) Reference: A-`` above the date row and the reference
    suffix ``A607F657-001`` below it. The position-based row band must
    pick up both wrap lines and combine them in print order.
    """
    matches = [
        r for r in feb_apr_rows
        if r.date == "2026-01-29" and "E.ON" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description == (
        "E.ON NEXT LTD (Direct Debit) Reference: A- A607F657-001"
    )
    assert r.money_out == "335.99"
    assert r.balance == "2508.89"


def test_monzobank_currency_conversion_description_assembled(
    feb_apr_rows: list[RawRow],
) -> None:
    """Currency-conversion rows wrap to three lines (description top,
    "Amount: EUR ... Exchange rate:" beside the date, "1.147706."
    below). All three pieces must land in the description field, and
    the ``-468.00.`` token (a money-shaped string with a trailing
    period) must NOT be picked up as the row's amount or balance — the
    row's real amount is the GBP-equivalent ``-407.77``.
    """
    matches = [r for r in feb_apr_rows if "BKG*Hotel" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2026-01-24"
    assert r.money_out == "407.77"
    assert r.balance == "2412.64"
    for fragment in (
        "BKG*Hotel at Booking.c London GBR",
        "Amount: EUR -468.00.",
        "Exchange rate:",
        "1.147706.",
    ):
        assert fragment in r.description, f"missing fragment {fragment!r}"


def test_monzobank_positive_amount_lands_in_money_in(
    feb_apr_rows: list[RawRow],
) -> None:
    """Direction is decided by the sign of the printed amount; the
    18 deposits printed without a leading minus must all land in
    money_in. A regression that ignored sign would surface here.
    """
    deposits = [r for r in feb_apr_rows if r.money_in]
    assert len(deposits) == 18
    for r in deposits:
        assert r.money_out == ""
        assert Decimal(r.money_in) > 0


def test_monzobank_balance_chain_reconstructs_closing(
    feb_apr_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the implied opening
    balance reproduces the printed closing balance, and every row's
    printed balance agrees with the running total at that point.
    """
    running = _OPENING_BALANCE
    for r in reversed(feb_apr_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_monzobank_no_chrome_leaks_into_rows(
    feb_apr_rows: list[RawRow],
) -> None:
    """Per-page chrome (the table header ``Date Description (GBP)
    Amount (GBP) Balance`` and the footer that opens with ``Monzo Bank
    Limited``) and page-1 account-summary text must not pollute any
    field. A regression in the body-band detection would surface here
    before it surfaced as a Decimal parse error in normalize.py.
    """
    for r in feb_apr_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "Monzo Bank Limited" not in value
            assert "Broadwalk" not in value
            assert "(GBP)" not in value
            assert "FSCS" not in value
            assert "Sort code" not in value
            assert "Account number" not in value


def test_monzobank_multipage_first_row_on_page_two(
    feb_apr_rows: list[RawRow],
) -> None:
    """The first row on page 2 (23/01/2026 NOMURA) must be present and
    correctly parsed — confirms each page's table is processed
    independently and the page-1 / page-2 boundary doesn't drop or
    merge rows.
    """
    matches = [
        r for r in feb_apr_rows
        if r.date == "2026-01-23" and "NOMURA" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_out == "7.95"
    assert r.balance == "2820.41"
