"""Tests for the HSBC PDF statement extractor.

Sample under test: ``tests/samples/August.pdf`` — a five-page HSBC UK
business current account statement covering 30 July to 29 August 2025.
The printed Account Summary on page 1 gives the ground-truth totals
that several tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.base import SummaryProvider
from statement_to_excel.extractors.hsbc import HsbcExtractor
from statement_to_excel.models import RawRow

# A synthetic two-day HSBC table exercising the OBP (online bill payment) and
# ATM (cash withdrawal) type codes, both of which were silently dropped before
# they were added to the extractor's known codes. Kept inline rather than as a
# PDF fixture so no real customer statement has to be committed.
_OBP_ATM_PAGE = """\
Date Payment type and details Paid out Paid in Balance
10 Feb 25 BP Nupur Lanjekar
January Invoice 1,320.00
OBP Cloud Odyssey It S
pisp1411485430 1,846.52
BP Hoshi Digital Ltd
January Invoice 9,900.00 49,604.27
18 Feb 25 ))) SQ *CHAI GUYS
London 2.94
ATM CASH NATWEST FEB18
LIVERPOOL ST@16:31 100.00 24,163.52
"""

# Hand-counted from tests/samples/August.pdf.
_EXPECTED_ROW_COUNT = 91
# Printed Account Summary block on page 1 of the sample.
_OPENING_BALANCE = Decimal("11966.79")
_CLOSING_BALANCE = Decimal("8435.10")
_TOTAL_PAID_IN = Decimal("19600.43")
_TOTAL_PAID_OUT = Decimal("23132.12")
_FIRST_DATE = datetime.date(2025, 7, 30)
_LAST_DATE = datetime.date(2025, 8, 29)


@pytest.fixture
def august_rows(samples_dir: Path) -> list[RawRow]:
    """Run the HSBC extractor on August.pdf once per test."""
    return HsbcExtractor().extract(samples_dir / "August.pdf")


def test_hsbc_august_row_count(august_rows: list[RawRow]) -> None:
    assert len(august_rows) == _EXPECTED_ROW_COUNT


def test_hsbc_august_first_row_is_newest(august_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row carries the closing balance."""
    first = august_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description.startswith("CR ")
    assert "TEYA" in first.description
    assert first.money_in == "308.01"
    assert first.money_out == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_hsbc_august_last_row_is_oldest(august_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = august_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description.startswith("BP ")
    assert "Karl" in last.description
    assert last.money_out == "213.68"
    assert last.money_in == ""


def test_hsbc_august_totals_match_account_summary(august_rows: list[RawRow]) -> None:
    """Sums of the paid-in / paid-out columns equal the printed totals."""
    total_in = sum(
        (Decimal(r.money_in) for r in august_rows if r.money_in), Decimal("0")
    )
    total_out = sum(
        (Decimal(r.money_out) for r in august_rows if r.money_out), Decimal("0")
    )
    assert total_in == _TOTAL_PAID_IN
    assert total_out == _TOTAL_PAID_OUT


def test_hsbc_august_exactly_one_direction_per_row(august_rows: list[RawRow]) -> None:
    """Every row sets exactly one of money_out / money_in (never both, never neither)."""
    for r in august_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_hsbc_august_dates_within_period(august_rows: list[RawRow]) -> None:
    for r in august_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_hsbc_august_no_marker_lines_leak_into_rows(august_rows: list[RawRow]) -> None:
    """The page-marker BALANCE BROUGHT/CARRIED FORWARD lines must not appear as rows."""
    for r in august_rows:
        upper = r.description.upper()
        assert "BROUGHT FORWARD" not in upper.replace(" ", "")
        assert "CARRIEDFORWARD" not in upper.replace(" ", "")
        assert "BALANCEBROUGHTFORWARD" not in upper.replace(" ", "")
        assert "BALANCECARRIEDFORWARD" not in upper.replace(" ", "")


def test_hsbc_obp_and_atm_codes_are_captured() -> None:
    """OBP and ATM rows must not be dropped (regression for silent loss)."""
    rows = HsbcExtractor().extract(Path("unused.pdf"), page_texts=[_OBP_ATM_PAGE])
    by_amount = {r.money_out: r for r in rows}

    obp = by_amount["1846.52"]
    assert obp.description.startswith("OBP ")
    assert "pisp1411485430" in obp.description
    assert obp.money_in == ""

    atm = by_amount["100.00"]
    assert atm.description.startswith("ATM ")
    assert "NATWEST" in atm.description.upper()
    assert atm.money_in == ""


def test_hsbc_summary_parses_account_summary() -> None:
    """summary() lifts the four printed totals out of the statement text."""
    page = (
        "OpeningBalance 53,561.50\n"
        "Payments In 138,576.18\n"
        "Payments Out 167,993.86\n"
        "ClosingBalance 24,143.82\n"
    )
    summary = HsbcExtractor().summary(Path("unused.pdf"), page_texts=[page])
    assert summary is not None
    assert summary.opening_balance == "53,561.50"
    assert summary.paid_in == "138,576.18"
    assert summary.paid_out == "167,993.86"
    assert summary.closing_balance == "24,143.82"


def test_hsbc_summary_absent_returns_none() -> None:
    """No summary block (e.g. a page with no totals) yields None, not blanks."""
    assert HsbcExtractor().summary(Path("unused.pdf"), page_texts=["no totals here"]) is None


def test_hsbc_extractor_is_a_summary_provider() -> None:
    """The pipeline relies on this isinstance check to decide to reconcile."""
    assert isinstance(HsbcExtractor(), SummaryProvider)


def test_hsbc_august_balance_chain_reconstructs_closing(august_rows: list[RawRow]) -> None:
    """Walking the rows chronologically from the opening balance reproduces
    the printed closing balance, and every row that prints a daily-end
    balance agrees with the running total at that point."""
    running = _OPENING_BALANCE
    for r in reversed(august_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        if r.balance:
            assert Decimal(r.balance) == running, (
                f"balance chain broke at {r.date} {r.description!r}: "
                f"running={running}, printed balance={r.balance}"
            )
    assert running == _CLOSING_BALANCE
