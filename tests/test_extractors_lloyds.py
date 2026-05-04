"""Tests for the Lloyds PDF statement extractor.

Sample under test: ``tests/samples/2024_August_Statement.pdf`` — a
single-page Lloyds Bank UK business current account statement covering
01 to 31 August 2024. The printed Account Summary at the top of the
statement gives the ground-truth Money In / Money Out totals that
several tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.lloyds import LloydsExtractor
from statement_to_excel.models import RawRow

# Hand-counted from tests/samples/2024_August_Statement.pdf.
_EXPECTED_ROW_COUNT = 5
# Printed Account Summary block at the top of the statement.
_TOTAL_PAID_IN = Decimal("0.00")
_TOTAL_PAID_OUT = Decimal("3384.94")
# "Balance on 31 August 2024" — end-of-period closing balance.
_PERIOD_END_BALANCE = Decimal("20514.19")
# First row in printed (oldest-first) order has balance equal to the
# pre-period closing balance minus its own outflow.
_OPENING_BALANCE = Decimal("23899.13")
_FIRST_DATE = datetime.date(2024, 8, 1)
_LAST_DATE = datetime.date(2024, 8, 21)


@pytest.fixture
def august_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Lloyds extractor on 2024_August_Statement.pdf once per test."""
    return LloydsExtractor().extract(samples_dir / "2024_August_Statement.pdf")


def test_lloyds_august_row_count(august_rows: list[RawRow]) -> None:
    assert len(august_rows) == _EXPECTED_ROW_COUNT


def test_lloyds_august_first_row_is_newest(august_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the period's last txn."""
    first = august_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description.startswith("FPO ")
    assert "HMRC VAT" in first.description
    # The multi-line description's continuation tokens must survive.
    assert "400000001412727376" in first.description
    assert first.money_out == "2865.21"
    assert first.money_in == ""
    assert first.balance == str(_PERIOD_END_BALANCE)


def test_lloyds_august_last_row_is_oldest(august_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = august_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == "SO GLOBAL ACCOUNTANTS"
    assert last.money_out == "50.00"
    assert last.money_in == ""


def test_lloyds_august_totals_match_account_summary(
    august_rows: list[RawRow],
) -> None:
    """Sums of the paid-in / paid-out columns equal the printed totals."""
    total_in = sum(
        (Decimal(r.money_in) for r in august_rows if r.money_in), Decimal("0")
    )
    total_out = sum(
        (Decimal(r.money_out) for r in august_rows if r.money_out), Decimal("0")
    )
    assert total_in == _TOTAL_PAID_IN
    assert total_out == _TOTAL_PAID_OUT


def test_lloyds_august_exactly_one_direction_per_row(
    august_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in (never both, never neither)."""
    for r in august_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_lloyds_august_dates_within_period(august_rows: list[RawRow]) -> None:
    for r in august_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_lloyds_august_no_label_tokens_leak_into_rows(
    august_rows: list[RawRow],
) -> None:
    """The accessibility-tag overlay ("Date", "Money", "Moneyb...", etc.) must
    not pollute any value field. These tokens are at predictable y-offsets
    from each row baseline; a regression in the chrome filter would surface
    here before it surfaces as a Decimal parse error in normalize.py."""
    for r in august_rows:
        for value in (r.date, r.money_out, r.money_in, r.balance):
            assert "Money" not in value
            assert "Description" not in value
            assert "Balance" not in value
            assert "Column" not in value
            assert "Ilna" not in value
            assert "Moneyb" not in value


def test_lloyds_august_balance_chain_reconstructs_closing(
    august_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the opening balance reproduces
    the printed closing balance, and every row's printed balance agrees with
    the running total at that point."""
    running = _OPENING_BALANCE
    for r in reversed(august_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _PERIOD_END_BALANCE
