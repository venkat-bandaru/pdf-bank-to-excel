"""Tests for the NatWest (National Westminster Bank) PDF extractor.

Sample under test: ``tests/samples/Statement-01-04-2025.pdf`` — a
two-page NatWest current-account statement covering the period
01 MAR 2025 to 01 APR 2025. The transaction table lives entirely on
page 1; page 2 is regulatory boilerplate. The printed Summary block
on page 1 gives the ground-truth totals the tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.natwestbank import NatWestBankExtractor
from statement_to_excel.models import RawRow

# Hand-counted from page 1 of Statement-01-04-2025.pdf.
_EXPECTED_ROW_COUNT = 15
# Printed "Summary" block on page 1.
_PREVIOUS_BALANCE = Decimal("6528.05")
_TOTAL_PAID_IN = Decimal("11700.00")
_TOTAL_WITHDRAWN = Decimal("15366.69")
_NEW_BALANCE = Decimal("2861.36")
_FIRST_DATE = datetime.date(2025, 3, 6)
_LAST_DATE = datetime.date(2025, 3, 31)


@pytest.fixture
def natwest_rows(samples_dir: Path) -> list[RawRow]:
    """Run the NatWest extractor on the sample once per test."""
    return NatWestBankExtractor().extract(
        samples_dir / "Statement-01-04-2025.pdf"
    )


def test_natwestbank_row_count(natwest_rows: list[RawRow]) -> None:
    assert len(natwest_rows) == _EXPECTED_ROW_COUNT


def test_natwestbank_first_row_is_newest(natwest_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the 31 MAR card txn."""
    first = natwest_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == (
        "Card Transaction 8227 29MAR25 WWW.CAPITAL ONTAP.COM LONDON GB"
    )
    assert first.money_out == "500.00"
    assert first.money_in == ""
    assert first.balance == str(_NEW_BALANCE)


def test_natwestbank_last_row_is_oldest(natwest_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = natwest_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == (
        "OnLine Transaction To A/C 20245165 KATUMALA V Via Mobile Xfer"
    )
    assert last.money_out == "500.00"
    assert last.money_in == ""
    assert last.balance == "6028.05"


def test_natwestbank_totals_match_summary(
    natwest_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the printed totals on page 1."""
    total_in = sum(
        (Decimal(r.money_in) for r in natwest_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in natwest_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_PAID_IN
    assert total_out == _TOTAL_WITHDRAWN


def test_natwestbank_exactly_one_direction_per_row(
    natwest_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in natwest_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_natwestbank_dates_within_period(natwest_rows: list[RawRow]) -> None:
    for r in natwest_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_natwestbank_paid_in_row_lands_in_money_in(
    natwest_rows: list[RawRow],
) -> None:
    """The 18 MAR LORIEN RESOURCING credit (£11,700.00) is the only deposit
    on this statement and must land in money_in. Direction is decided by
    balance differential, so a regression that mis-classified the column
    would surface here.
    """
    deposits = [r for r in natwest_rows if r.money_in]
    assert len(deposits) == 1
    r = deposits[0]
    assert r.date == "2025-03-18"
    assert "LORIEN RESOURCING" in r.description
    assert r.money_in == "11700.00"
    assert r.money_out == ""
    assert r.balance == "11859.41"


def test_natwestbank_multiline_description_folded(
    natwest_rows: list[RawRow],
) -> None:
    """Descriptions that wrap across two printed lines must end up as a
    single description field. The 06 MAR OnLine Transaction prints
    ``...KATUMALA V Via`` on one line and ``Mobile Xfer`` + the amount
    on the next; both pieces must be present.
    """
    matches = [
        r for r in natwest_rows
        if r.date == "2025-03-06" and "Mobile Xfer" in r.description
        and "VK Transfer" not in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description == (
        "OnLine Transaction To A/C 20245165 KATUMALA V Via Mobile Xfer"
    )
    assert r.money_out == "500.00"
    assert r.balance == "6028.05"


def test_natwestbank_same_date_inheritance(
    natwest_rows: list[RawRow],
) -> None:
    """Transactions that share a date with the prior row print no date
    prefix; they must inherit the previous row's date. Three rows print
    on 06 MAR but only the first carries the ``06 MAR`` prefix.
    """
    march_six = [r for r in natwest_rows if r.date == "2025-03-06"]
    assert len(march_six) == 3
    descriptions = {r.description for r in march_six}
    assert any("HMRC VAT" in d for d in descriptions)
    assert any("GLOBAL ACCOUNTANTS" in d for d in descriptions)
    assert any(
        "Mobile Xfer" in d and "VK Transfer" not in d
        for d in descriptions
    )


def test_natwestbank_balance_chain_reconstructs_closing(
    natwest_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed Previous Balance
    reproduces the printed New Balance, and every row's printed balance
    agrees with the running total at that point.
    """
    running = _PREVIOUS_BALANCE
    for r in reversed(natwest_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _NEW_BALANCE


def test_natwestbank_no_chrome_leaks_into_rows(
    natwest_rows: list[RawRow],
) -> None:
    """Per-page chrome (the table header, the Summary box, the regulatory
    footer that opens with ``National Westminster Bank Plc.``) and the
    page 2 boilerplate must not pollute any field.
    """
    for r in natwest_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "National Westminster Bank Plc" not in value
            assert "BROUGHT FORWARD" not in value
            assert "Paid In" not in value
            assert "Withdrawn(" not in value
            assert "Statement Date" not in value
            assert "Period Covered" not in value
            assert "FSCS" not in value
            assert "Authorised by" not in value


def test_natwestbank_brought_forward_is_not_a_transaction(
    natwest_rows: list[RawRow],
) -> None:
    """The ``01 MAR 2025 BROUGHT FORWARD 6,528.05`` opening line carries
    a single money token (the opening balance) and is not a transaction;
    it must not appear as a row, but its balance must seed the direction
    detection so the very first transaction's direction is correct.
    """
    for r in natwest_rows:
        assert "BROUGHT FORWARD" not in r.description
    # The chronologically-earliest row (last in the newest-first list)
    # is the 06 MAR 500.00 withdrawal — money_out, not money_in. A
    # missing opening balance would have left direction undetermined,
    # causing this row to fall through to the default.
    assert natwest_rows[-1].money_out == "500.00"
    assert natwest_rows[-1].money_in == ""
