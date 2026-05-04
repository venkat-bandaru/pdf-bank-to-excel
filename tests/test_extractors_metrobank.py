"""Tests for the Metrobank PDF statement extractor.

Sample under test: ``tests/samples/IAG Aug 24.pdf`` — a four-page Metro
Bank UK business bank account statement covering 01 AUG 2024 to
31 AUG 2024. The transaction table lives on pages 3-4; pages 1-2 hold
the fees-and-charges breakdown and must not be parsed as transactions.
The printed account summary on page 3 gives the ground-truth totals
the tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.metrobank import MetrobankExtractor
from statement_to_excel.models import RawRow

# Hand-counted from tests/samples/IAG Aug 24.pdf.
_EXPECTED_ROW_COUNT = 7
# Printed "Your account summary" block on page 3 of the sample.
_OPENING_BALANCE = Decimal("-15229.80")
_CLOSING_BALANCE = Decimal("-14813.41")
_TOTAL_PAID_IN = Decimal("13728.00")
_TOTAL_PAID_OUT = Decimal("13311.61")
_FIRST_DATE = datetime.date(2024, 8, 6)
_LAST_DATE = datetime.date(2024, 8, 28)


@pytest.fixture
def aug_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Metrobank extractor on IAG Aug 24.pdf once per test."""
    return MetrobankExtractor().extract(samples_dir / "IAG Aug 24.pdf")


def test_metrobank_aug_row_count(aug_rows: list[RawRow]) -> None:
    assert len(aug_rows) == _EXPECTED_ROW_COUNT


def test_metrobank_aug_first_row_is_newest(aug_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is 28 AUG's last txn."""
    first = aug_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == "Debit Interest"
    assert first.money_out == "178.07"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_metrobank_aug_last_row_is_oldest(aug_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest transaction."""
    last = aug_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description.startswith("Outward Faster Payment HMRC VAT")
    # The multi-line continuation tokens must survive into the description.
    assert "GOVERNMENT BANKING" in last.description
    assert last.money_out == "1124.27"
    assert last.money_in == ""
    assert last.balance == "-16354.07"


def test_metrobank_aug_totals_match_account_summary(
    aug_rows: list[RawRow],
) -> None:
    """Sums of the paid-in / paid-out columns equal the printed totals."""
    total_in = sum(
        (Decimal(r.money_in) for r in aug_rows if r.money_in), Decimal("0")
    )
    total_out = sum(
        (Decimal(r.money_out) for r in aug_rows if r.money_out), Decimal("0")
    )
    assert total_in == _TOTAL_PAID_IN
    assert total_out == _TOTAL_PAID_OUT


def test_metrobank_aug_exactly_one_direction_per_row(
    aug_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in (never both, never neither)."""
    for r in aug_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_metrobank_aug_dates_within_period(aug_rows: list[RawRow]) -> None:
    for r in aug_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_metrobank_aug_bacs_payment_classified_as_money_in(
    aug_rows: list[RawRow],
) -> None:
    """The single inbound BACS payment must land in money_in.

    The balance-differential check decides direction; a regression that
    inverted the sign would surface here as the only credit on the
    statement leaking into money_out before it surfaced as a balance-
    chain failure.
    """
    bacs = [r for r in aug_rows if r.description.startswith("BACS Payment")]
    assert len(bacs) == 1
    r = bacs[0]
    assert r.money_in == "13728.00"
    assert r.money_out == ""
    assert r.date == "2024-08-12"


def test_metrobank_aug_multiline_description_assembled(
    aug_rows: list[RawRow],
) -> None:
    """A description split across four lines must fold into one field.

    The 19 AUG outward faster payment prints across four lines:
    ``Outward Faster Payment SHANDRAWAR IT / TECHNOLOGIES PVT LTD /
    BARCLAYS BANK UK PLC / IAG019``. All four pieces must land in
    the description field.
    """
    matching = [r for r in aug_rows if "SHANDRAWAR" in r.description]
    assert len(matching) == 1
    r = matching[0]
    assert r.description.startswith("Outward Faster Payment")
    assert "TECHNOLOGIES PVT LTD" in r.description
    assert "BARCLAYS BANK UK PLC" in r.description
    assert "IAG019" in r.description
    assert r.date == "2024-08-19"
    assert r.money_out == "10982.40"


def test_metrobank_aug_no_chrome_leaks_into_rows(
    aug_rows: list[RawRow],
) -> None:
    """Per-page chrome (the ``MBS3C_...`` footer code, "Statement number",
    "Sort code", "Business Bank Account number", "Balance brought forward",
    and "Closing Balance" markers) must not pollute any field. A regression
    in the chrome filter would surface here before it surfaced as a
    Decimal parse error in normalize.py.
    """
    for r in aug_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "MBS3C_" not in value
            assert "Statement number" not in value
            assert "Sort code" not in value
            assert "Business Bank Account number" not in value
            assert "brought forward" not in value.lower()
            assert "closing balance" not in value.lower()


def test_metrobank_aug_fees_page_not_parsed_as_transactions(
    aug_rows: list[RawRow],
) -> None:
    """Pages 1-2 hold the fees-and-charges breakdown (with rows like
    "Outward Faster Payment SAMEDAY 2 0.30 0.60" that look superficially
    like transactions). The "Date Transaction Money out" header gates
    table mode, so those rows must not appear as parsed transactions."""
    descriptions = " | ".join(r.description for r in aug_rows)
    assert "SAMEDAY" not in descriptions
    assert "Sub Total" not in descriptions
    assert "Less Free" not in descriptions
    assert "Volume" not in descriptions


def test_metrobank_aug_balance_chain_reconstructs_closing(
    aug_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the opening balance reproduces
    the printed closing balance, and every row's printed balance agrees
    with the running total at that point."""
    running = _OPENING_BALANCE
    for r in reversed(aug_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE
