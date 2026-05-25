"""Tests for the Royal Bank of Scotland PDF statement extractor.

Sample under test: ``tests/samples/rbs_bank.pdf`` — a two-page RBS
"Business Current Account" statement covering 28 Feb 2026 through
1 Apr 2026. Page 1 carries the entire transaction table; page 2 is
marketing / regulatory boilerplate with no transactions. The page-1
summary block prints the previous balance, total paid in, total
withdrawn, and new balance figures the tests below pin to; the
printed running-balance column on the rightmost edge of every row
gives the balance-chain ground truth.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.rbsbank import RbsBankExtractor
from statement_to_excel.models import RawRow

_SAMPLE = "rbs_bank.pdf"

# Hand-counted from the printed transaction table on page 1, excluding
# the table header, the "DD MON YYYY BROUGHT FORWARD" opening-balance
# row (carries no transaction), and the regulatory footer block.
_EXPECTED_ROW_COUNT = 14
# Printed in the page-1 summary block.
_OPENING_BALANCE = Decimal("47.89")
_CLOSING_BALANCE = Decimal("20.15")
_TOTAL_CREDITS = Decimal("8741.00")
_TOTAL_DEBITS = Decimal("8768.74")
_FIRST_DATE = datetime.date(2026, 3, 2)
_LAST_DATE = datetime.date(2026, 4, 1)


@pytest.fixture
def rbs_rows(samples_dir: Path) -> list[RawRow]:
    """Run the RBS extractor on the sample once per test."""
    return RbsBankExtractor().extract(samples_dir / _SAMPLE)


def test_rbsbank_row_count(rbs_rows: list[RawRow]) -> None:
    assert len(rbs_rows) == _EXPECTED_ROW_COUNT


def test_rbsbank_first_row_is_newest(rbs_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the very first row is the
    01 APR PARATUS direct debit that lands on the printed closing
    balance. RBS prints rows oldest-first, so this also confirms the
    reverse-on-emit step.
    """
    first = rbs_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert "Direct Debit PARATUS AMC LTD" in first.description
    assert "212320009" in first.description
    assert first.money_out == "705.39"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_rbsbank_last_row_is_oldest(rbs_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest
    transaction (02 MAR Automated Credit). Its direction is the only
    one that depends on the BROUGHT FORWARD seed for prev_balance,
    so this row exercises the opening-balance path in
    ``_split_directions``.
    """
    last = rbs_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert "Automated Credit" in last.description
    assert "SUDHAKAR THOTAMALL" in last.description
    assert last.money_in == "1355.00"
    assert last.money_out == ""
    assert last.balance == "1402.89"


def test_rbsbank_totals_match_summary(rbs_rows: list[RawRow]) -> None:
    """Sums of money_in / money_out match the page-1 summary's
    ``Paid In £8,741.00`` and ``Withdrawn £8,768.74`` figures. A row
    misclassified IN-as-OUT (or vice versa) would shift both totals
    away from these values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in rbs_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in rbs_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_CREDITS
    assert total_out == _TOTAL_DEBITS


def test_rbsbank_exactly_one_direction_per_row(
    rbs_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in rbs_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_rbsbank_dates_within_period(rbs_rows: list[RawRow]) -> None:
    for r in rbs_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_rbsbank_balance_chain_reconstructs_closing(
    rbs_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed previous
    balance reproduces the printed new balance, and every row's
    printed balance agrees with the running total at that point.
    A balance break would surface here before it surfaced as a "low"
    confidence flag in normalize.py.
    """
    running = _OPENING_BALANCE
    for r in reversed(rbs_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_rbsbank_date_inheritance_across_rows(
    rbs_rows: list[RawRow],
) -> None:
    """The 02 MAR block prints the date only on the first of its
    three transactions; the next two rows carry "Direct Debit ..."
    with no leading date and must inherit "02 MAR". A regression in
    the date-carry logic would land those rows on a different date
    or on the empty string.
    """
    march_2_rows = [r for r in rbs_rows if r.date == "2026-03-02"]
    assert len(march_2_rows) == 3
    descriptions = {r.description for r in march_2_rows}
    assert any("Automated Credit" in d for d in descriptions)
    assert sum("Direct Debit PARATUS" in d for d in descriptions) == 2


def test_rbsbank_multiline_description_folded(
    rbs_rows: list[RawRow],
) -> None:
    """The 10 MAR LAMONT row wraps its description over two text
    lines ("Automated Credit LAMONT ESTA LTD 46 CARNATION" then
    "PLACE FP 10/03/26 ..."). Both lines must be joined into the
    description; the trailing money tokens on the second line must
    be stripped before joining.
    """
    matches = [
        r for r in rbs_rows
        if r.date == "2026-03-10" and "LAMONT" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert "Automated Credit LAMONT ESTA LTD 46 CARNATION PLACE" in r.description
    assert "MF010714643702" in r.description
    # The amount/balance tokens must NOT appear inside the description.
    assert "1,056.00" not in r.description
    assert "1,060.13" not in r.description
    assert r.money_in == "1056.00"
    assert r.balance == "1060.13"


def test_rbsbank_single_line_row_with_date_prefix(
    rbs_rows: list[RawRow],
) -> None:
    """The 20 MAR Charges row fits everything on a single anchor
    line (date + description + amount + balance). A regression in
    the same-line termination branch would either drop this row or
    fold it into the next row's description.
    """
    matches = [r for r in rbs_rows if "Charges" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2026-03-20"
    assert r.description == "Charges 27FEB A/C 10993560"
    assert r.money_out == "4.55"
    assert r.balance == "2111.58"


def test_rbsbank_no_chrome_leaks_into_rows(
    rbs_rows: list[RawRow],
) -> None:
    """The page-1 customer block (account name, sort code, statement
    period, "Previous Balance" / "Paid In" / "Withdrawn" / "New
    Balance" summary, BIC / IBAN), the per-page table header, the
    BROUGHT FORWARD opening-balance row, and the per-page
    "The Royal Bank of Scotland plc." regulatory footer must not
    pollute any field. A regression in ``_filter_chrome`` would
    surface here.
    """
    for r in rbs_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "ASPIRE SMART" not in value
            assert "TORRINGTON" not in value
            assert "COVENTRY" not in value
            assert "Account Name" not in value
            assert "Sort Code" not in value
            assert "Statement Date" not in value
            assert "Period Covered" not in value
            assert "Previous Balance" not in value
            assert "New Balance" not in value
            assert "Paid In(" not in value
            assert "Withdrawn(" not in value
            assert "Balance(" not in value
            assert "BIC " not in value
            assert "IBAN" not in value
            assert "BROUGHT FORWARD" not in value
            assert "Royal Bank of Scotland" not in value
            assert "Financial Conduct Authority" not in value


def test_rbsbank_brought_forward_row_excluded(
    rbs_rows: list[RawRow],
) -> None:
    """The BROUGHT FORWARD opening-balance row is captured for its
    year and seed-balance but must not be emitted as a transaction.
    A regression would surface as an extra row with the opening
    balance value (47.89) appearing in money_in / money_out and an
    empty other direction column.
    """
    for r in rbs_rows:
        assert r.money_in != "47.89"
        assert r.money_out != "47.89"
        assert "BROUGHT" not in r.description.upper()
