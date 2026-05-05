"""Tests for the Revolut Business PDF statement extractor.

Sample under test: ``tests/samples/revoult_01-Oct-2025.pdf`` — a
seven-page Revolut Business GBP account statement covering 1 October
2025 through 31 December 2025. Pages 1-7 carry the transaction table;
the trailing "Transaction types" block on page 7 carries the
ground-truth per-direction totals the tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.revolutbank import RevolutBankExtractor
from statement_to_excel.models import RawRow

# Hand-counted from the seven pages of the sample.
_EXPECTED_ROW_COUNT = 184
# Printed "Balance summary" block on page 1.
_OPENING_BALANCE = Decimal("198039.66")
_CLOSING_BALANCE = Decimal("189924.28")
_TOTAL_MONEY_IN = Decimal("8.00")
_TOTAL_MONEY_OUT = Decimal("8123.38")
_FIRST_DATE = datetime.date(2025, 10, 1)
_LAST_DATE = datetime.date(2025, 12, 31)


@pytest.fixture
def revolut_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Revolut extractor on the sample once per test."""
    return RevolutBankExtractor().extract(
        samples_dir / "revoult_01-Oct-2025.pdf"
    )


def test_revolutbank_row_count(revolut_rows: list[RawRow]) -> None:
    assert len(revolut_rows) == _EXPECTED_ROW_COUNT


def test_revolutbank_first_row_is_newest(revolut_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the 31 Dec card txn
    whose printed balance equals the statement's closing balance."""
    first = revolut_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == "Varanasi"
    assert first.money_out == "115.80"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_revolutbank_last_row_is_oldest(revolut_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = revolut_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert "Global Accountants UK Ltd" in last.description
    assert "Monthly fres" in last.description
    assert last.money_out == "70.00"
    assert last.money_in == ""
    assert last.balance == "197969.66"


def test_revolutbank_totals_match_balance_summary(
    revolut_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the printed totals on page 1."""
    total_in = sum(
        (Decimal(r.money_in) for r in revolut_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in revolut_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_MONEY_IN
    assert total_out == _TOTAL_MONEY_OUT


def test_revolutbank_exactly_one_direction_per_row(
    revolut_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in revolut_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_revolutbank_dates_within_period(revolut_rows: list[RawRow]) -> None:
    for r in revolut_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_revolutbank_mor_lands_in_money_in(revolut_rows: list[RawRow]) -> None:
    """The 12 Nov MOR (Money received) Trainline refund is the only deposit
    on this statement and must land in money_in. Direction is decided by
    the type code, so a regression that mis-classified MOR would surface
    here (and in test_revolutbank_balance_chain_reconstructs_closing).
    """
    deposits = [r for r in revolut_rows if r.money_in]
    assert len(deposits) == 1
    r = deposits[0]
    assert r.date == "2025-11-12"
    assert r.description == "Refund from Trainline"
    assert r.money_in == "8.00"
    assert r.money_out == ""
    assert r.balance == "196004.88"


def test_revolutbank_thousands_separator_stripped(
    revolut_rows: list[RawRow],
) -> None:
    """Revolut prints £195 017.33 (space thousands separator); the
    cleaned token must drop the space so normalize.py can parse it
    with Decimal()."""
    sample = next(r for r in revolut_rows if r.balance == "189924.28")
    assert " " not in sample.balance
    # Argos at £1 199.00 is the only four-digit transaction amount in the
    # sample — exercises the same cleanup on the amount column.
    argos = next(r for r in revolut_rows if "Argos" in r.description)
    assert argos.money_out == "1199.00"


def test_revolutbank_multiline_description_folded(
    revolut_rows: list[RawRow],
) -> None:
    """A description wrap that prints below the date row must end up as a
    single description field. ``To The badminton club at highwoods •`` and
    its wrap suffix ``Badminton4mVinay`` print on consecutive lines and
    must be combined; the row appears three times in the sample (1 Oct,
    1 Nov and 1 Dec) so the test pins each.
    """
    matches = [r for r in revolut_rows if "Badminton4mVinay" in r.description]
    assert {r.date for r in matches} == {"2025-10-01", "2025-11-01", "2025-12-01"}
    for r in matches:
        assert r.description == (
            "To The badminton club at highwoods • Badminton4mVinay"
        )
        assert r.money_out == "54.00"


def test_revolutbank_hmrc_wrap_folded(revolut_rows: list[RawRow]) -> None:
    """The 22 Oct HMRC payment wraps its reference (XB003049669787) onto a
    second line; the wrap must fold into the description, not start a
    spurious row.
    """
    matches = [r for r in revolut_rows if "HMRC" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2025-10-22"
    assert "XB003049669787" in r.description
    assert r.money_out == "37.44"
    assert r.balance == "196852.08"


def test_revolutbank_fx_rate_continuation_does_not_break_amount(
    revolut_rows: list[RawRow],
) -> None:
    """A foreign-currency card transaction prints an extra line of the
    form ``FX Rate GBP 1 = EUR <rate> €<amount>`` below the date row.
    The € token must NOT be picked up as the row's amount (the GBP
    amount on the date line above is the real value), and the balance
    must still be the trailing £ token.
    """
    matches = [
        r for r in revolut_rows
        if r.date == "2025-11-22" and "Ryanair" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_out == "3.09"
    assert r.balance == "195017.33"
    assert "FX Rate GBP 1 = EUR 1.133296" in r.description


def test_revolutbank_balance_chain_reconstructs_closing(
    revolut_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed opening balance
    reproduces the printed closing balance, and every row's printed
    balance agrees with the running total at that point. This is the
    single strongest proof that direction classification, amount
    parsing, and row ordering are all correct.
    """
    running = _OPENING_BALANCE
    for r in reversed(revolut_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_revolutbank_no_chrome_leaks_into_rows(
    revolut_rows: list[RawRow],
) -> None:
    """Per-page chrome (the regulatory footer that opens with ``Revolut
    Ltd is registered``, the QR-code helper text, the page number, the
    balance-summary block above the table) must not pollute any field.
    A regression in the gating or footer-filter logic would surface
    here before it surfaced as a Decimal parse error in normalize.py.
    """
    for r in revolut_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "Revolut Ltd" not in value
            assert "Report lost" not in value
            assert "Get help directly" not in value
            assert "Scan the QR" not in value
            assert "Date (UTC)" not in value
            assert "Opening balance" not in value
            assert "Closing balance" not in value
            assert "Transaction types" not in value


def test_revolutbank_multipage_first_row_on_page_two(
    revolut_rows: list[RawRow],
) -> None:
    """The first row on page 2 (30 Dec 2025 Nyx*lakedistrictnation) must
    be present and correctly parsed — confirms the page-1 / page-2
    boundary doesn't drop or merge rows when the per-page footer
    intervenes between them.
    """
    matches = [
        r for r in revolut_rows
        if r.date == "2025-12-30" and "Nyx" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description == "Nyx*lakedistrictnation"
    assert r.money_out == "0.60"
    assert r.balance == "190497.89"
