"""Tests for the Virgin Money PDF statement extractor.

Sample under test: ``tests/samples/VirginMoney_Statement_2025-01-31.pdf``
— a three-page Virgin Money "Standard Business" statement covering
01 January 2025 through 31 January 2025. The "Total debits",
"Total credits" and "Closing Balance" lines on the final page give
the ground-truth totals the tests below pin to, and the
"Opening Balance" row at the top of page 1 supplies the initial
balance used for the balance-chain reconstruction.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.virginmoneybank import (
    VirginMoneyBankExtractor,
)
from statement_to_excel.models import RawRow

_SAMPLE = "VirginMoney_Statement_2025-01-31.pdf"

# Hand-counted from the three pages of the sample. Page 1 carries 35
# transaction rows after the "Opening Balance" row (which has only
# one £ token and is correctly excluded), page 2 carries 45, page 3
# carries 9 — total 89.
_EXPECTED_ROW_COUNT = 89
# Printed totals block on page 3.
_OPENING_BALANCE = Decimal("16873.02")
_CLOSING_BALANCE = Decimal("10770.74")
_TOTAL_DEBITS = Decimal("63923.99")
_TOTAL_CREDITS = Decimal("57821.71")
_FIRST_DATE = datetime.date(2025, 1, 2)
_LAST_DATE = datetime.date(2025, 1, 31)


@pytest.fixture
def virgin_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Virgin Money extractor on the sample once per test."""
    return VirginMoneyBankExtractor().extract(samples_dir / _SAMPLE)


def test_virginmoneybank_row_count(virgin_rows: list[RawRow]) -> None:
    assert len(virgin_rows) == _EXPECTED_ROW_COUNT


def test_virginmoneybank_first_row_is_newest(virgin_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the second of
    the two 31/01 ``MOB, Koteswara Nallamot`` Transfer rows — the one
    that drops the balance to the printed closing figure.
    """
    first = virgin_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == "MOB, Koteswara Nallamot, Koti Transfer"
    assert first.money_out == "2000.00"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_virginmoneybank_last_row_is_oldest(virgin_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest
    transaction (02/01 ``AX7581191376``). This row also exercises the
    description-internal-numbers case: ``1.90 0.02`` are not
    £-prefixed and must remain inside the description, not be picked
    up as the row's amount or balance.
    """
    last = virgin_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == "AX7581191376 1.90 0.02 Giro"
    assert last.money_in == "1.88"
    assert last.money_out == ""
    assert last.balance == "16874.90"


def test_virginmoneybank_totals_match_summary(
    virgin_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the printed Total credits /
    Total debits figures on page 3. This is the strongest single
    proof that balance-differential direction recovery is right — a
    row misclassified IN-as-OUT (or vice versa) would shift both
    totals away from these printed values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in virgin_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in virgin_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_CREDITS
    assert total_out == _TOTAL_DEBITS


def test_virginmoneybank_exactly_one_direction_per_row(
    virgin_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in virgin_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_virginmoneybank_dates_within_period(
    virgin_rows: list[RawRow],
) -> None:
    for r in virgin_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_virginmoneybank_opening_balance_row_excluded(
    virgin_rows: list[RawRow],
) -> None:
    """The "01 Jan 25 Opening Balance £16873.02" row carries only one
    £ token and is not a transaction; it must not appear in the
    extracted rows. A regression that included it would surface as an
    extra row dated 2025-01-01 with the description "Opening Balance".
    """
    assert not any(r.date == "2025-01-01" for r in virgin_rows)
    assert not any("Opening Balance" in r.description for r in virgin_rows)


def test_virginmoneybank_multiline_description_folded(
    virgin_rows: list[RawRow],
) -> None:
    """The 06/01 Boutin Holdings row wraps its description across two
    print lines (``Boutin Holdings Lt, 281 PREMISES`` / ``RENT
    Standing Order …``). The continuation must fold into the
    description; a regression that didn't fold would either drop the
    row (regex would not match the unfolded first line) or split it
    into two rows.
    """
    matches = [r for r in virgin_rows if "Boutin Holdings" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2025-01-06"
    assert r.description == (
        "Boutin Holdings Lt, 281 PREMISES RENT Standing Order"
    )
    assert r.money_out == "1700.00"
    assert r.money_in == ""
    assert r.balance == "6461.35"


def test_virginmoneybank_large_debit_direction(
    virgin_rows: list[RawRow],
) -> None:
    """The 03/01 Ag Parfett Direct Debit drops the balance from
    £12961.29 to £3561.51 — a £9399.78 outflow. A regression in the
    balance-differential classifier would land this in money_in.
    """
    matches = [
        r for r in virgin_rows
        if r.date == "2025-01-03" and "Ag Parfett" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_out == "9399.78"
    assert r.money_in == ""
    assert r.balance == "3561.51"


def test_virginmoneybank_large_credit_direction(
    virgin_rows: list[RawRow],
) -> None:
    """The 31/01 POST OFFICE Giro lifts the balance by £4948.44 — the
    only four-figure credit in the statement. Confirms positive
    differentials are classified as money_in.
    """
    matches = [r for r in virgin_rows if "POST OFFICE" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2025-01-31"
    assert r.money_in == "4948.44"
    assert r.money_out == ""
    assert r.balance == "19182.16"


def test_virginmoneybank_balance_chain_reconstructs_closing(
    virgin_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed opening
    balance reproduces the printed closing balance, and every row's
    printed balance agrees with the running total at that point.
    A balance break would surface here before it surfaced as a "low"
    confidence flag in normalize.py.
    """
    running = _OPENING_BALANCE
    for r in reversed(virgin_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_virginmoneybank_no_chrome_leaks_into_rows(
    virgin_rows: list[RawRow],
) -> None:
    """The page-1 customer-info block, the table header, and the
    page-3 totals/legend block must not pollute any field. A
    regression in ``_filter_chrome`` would surface here.
    """
    for r in virgin_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "STANDARD BUSINESS" not in value
            assert "SUNRISES HOLDINGS LTD" not in value
            assert "UPPER WORTLEY ROAD" not in value
            assert "Account number" not in value
            assert "Sort code" not in value
            assert "Statement date" not in value
            assert "Date Description" not in value
            assert "Total debits" not in value
            assert "Total credits" not in value
            assert "Closing Balance" not in value
            assert "Important information" not in value
            assert "Virgin Money" not in value


def test_virginmoneybank_multipage_first_row_on_page_two(
    virgin_rows: list[RawRow],
) -> None:
    """The first row on page 2 (13/01 ``Bcard1767066100125``, IN
    £1623.45) must be present and correctly classified — confirms
    the page-1 / page-2 boundary doesn't drop the row and that the
    balance-differential direction recovery keeps working when the
    previous balance comes from the last row on the previous page
    (£2293.06).
    """
    matches = [
        r for r in virgin_rows
        if r.date == "2025-01-13" and "Bcard1767066100125" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_in == "1623.45"
    assert r.money_out == ""
    assert r.balance == "3916.51"
