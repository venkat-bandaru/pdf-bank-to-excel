"""Tests for the Zempler Bank PDF statement extractor.

Sample under test: ``tests/samples/Zempler_transactions_062025.pdf``
— a single-page Zempler "Business Account" statement covering
01 June 2025 through 30 June 2025. The "Opening Balance" /
"Closing Balance" lines in the page header give the ground-truth
opening and closing figures the tests below pin to; the printed
running-balance column on the rightmost edge of each row gives the
balance-chain ground truth.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.zemplerbank import ZemplerBankExtractor
from statement_to_excel.models import RawRow

_SAMPLE = "Zempler_transactions_062025.pdf"

# Hand-counted from the sample's transaction table (excluding the
# header row "Date Card ending in Description Amount Balance" and
# the regulatory footer block).
_EXPECTED_ROW_COUNT = 17
# Printed balance summary in the page header.
_OPENING_BALANCE = Decimal("47.58")
_CLOSING_BALANCE = Decimal("-39.42")
# Hand-summed from the printed Amount column.
_TOTAL_DEBITS = Decimal("2006.00")
_TOTAL_CREDITS = Decimal("1919.00")
_FIRST_DATE = datetime.date(2025, 6, 1)
_LAST_DATE = datetime.date(2025, 6, 30)


@pytest.fixture
def zempler_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Zempler extractor on the sample once per test."""
    return ZemplerBankExtractor().extract(samples_dir / _SAMPLE)


def test_zemplerbank_row_count(zempler_rows: list[RawRow]) -> None:
    assert len(zempler_rows) == _EXPECTED_ROW_COUNT


def test_zemplerbank_first_row_is_newest(zempler_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the 25/06
    Dojo Direct Debit that lands on the printed closing balance.
    """
    first = zempler_rows[0]
    assert first.date == "2025-06-25"
    assert "DD:GC re Dojo" in first.description
    assert first.money_out == "48.95"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_zemplerbank_last_row_is_oldest(zempler_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest
    transaction (01/06 ``Fin: 85 LAUCHOPE STREET,AIRDRIE``). This
    row also exercises the description-internal-numbers case: ``85``
    has no £ prefix and must remain inside the description, not be
    picked up as the row's amount or balance.
    """
    last = zempler_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == "Fin: 85 LAUCHOPE STREET,AIRDRIE"
    assert last.money_in == "1265.00"
    assert last.money_out == ""
    assert last.balance == "1312.58"


def test_zemplerbank_totals_match_summary(
    zempler_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the hand-summed Amount
    column. A row misclassified IN-as-OUT (or vice versa) would
    shift both totals away from these values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in zempler_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in zempler_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_CREDITS
    assert total_out == _TOTAL_DEBITS


def test_zemplerbank_exactly_one_direction_per_row(
    zempler_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in zempler_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_zemplerbank_dates_within_period(
    zempler_rows: list[RawRow],
) -> None:
    for r in zempler_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_zemplerbank_large_debit_direction(
    zempler_rows: list[RawRow],
) -> None:
    """The 02/06 Capital on Tap Direct Debit drops the balance by
    £1221.45 — the largest outflow in the statement and the only
    one that requires comma handling on the way in. A regression in
    sign-based direction or in comma stripping would surface here.
    """
    matches = [
        r for r in zempler_rows
        if r.date == "2025-06-02" and "Capital on Tap" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_out == "1221.45"
    assert r.money_in == ""
    assert r.balance == "91.13"


def test_zemplerbank_large_credit_direction(
    zempler_rows: list[RawRow],
) -> None:
    """The 01/06 ``Fin: 85 LAUCHOPE STREET`` cash load lifts the
    balance by £1265.00. Confirms positive (unsigned) amounts are
    classified as money_in.
    """
    matches = [r for r in zempler_rows if "LAUCHOPE" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2025-06-01"
    assert r.money_in == "1265.00"
    assert r.money_out == ""


def test_zemplerbank_negative_balance_preserved(
    zempler_rows: list[RawRow],
) -> None:
    """Several rows in the sample carry a negative running balance
    (the account dips below zero on 10/06 and stays there until the
    22/06 USAVE credit). The printed minus sign must survive
    extraction; a regression in ``_strip_money`` would land these as
    positive balances.
    """
    matches = [
        r for r in zempler_rows
        if r.date == "2025-06-10" and "Bell Fire" in r.description
    ]
    assert len(matches) == 1
    assert matches[0].balance == "-39.78"


def test_zemplerbank_balance_chain_reconstructs_closing(
    zempler_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed opening
    balance reproduces the printed closing balance, and every row's
    printed balance agrees with the running total at that point.
    A balance break would surface here before it surfaced as a "low"
    confidence flag in normalize.py.
    """
    running = _OPENING_BALANCE
    for r in reversed(zempler_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_zemplerbank_no_chrome_leaks_into_rows(
    zempler_rows: list[RawRow],
) -> None:
    """The page header (customer name, address, "Opening Balance"
    summary, statement period) and the regulatory footer must not
    pollute any field. A regression in ``_filter_chrome`` would
    surface here.
    """
    for r in zempler_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "Harish Darapanani" not in value
            assert "Sannox Drive" not in value
            assert "Jph Traders" not in value
            assert "Sort code" not in value
            assert "Account number" not in value
            assert "Opening Balance" not in value
            assert "Closing Balance" not in value
            assert "Date Card ending" not in value
            assert "Zempler Bank" not in value
            assert "Financial Conduct Authority" not in value


def test_zemplerbank_card_suffix_not_in_description(
    zempler_rows: list[RawRow],
) -> None:
    """The "Card ending in" column (a four-digit suffix like "1495")
    is structural metadata, not part of the description. A
    regression in the row regex that bound the card digits into the
    description group would surface here.
    """
    for r in zempler_rows:
        assert not r.description.startswith("1495 ")
        assert not r.description.startswith("1495\t")
