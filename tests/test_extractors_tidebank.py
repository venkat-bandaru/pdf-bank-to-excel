"""Tests for the Tide / ClearBank PDF statement extractor.

Sample under test: ``tests/samples/tide_bank.pdf`` — a two-page Tide
"Bank statement" covering 1 Feb 2026 through 30 Apr 2026. The page-1
summary block prints the opening and closing balance and the total
paid in / paid out figures the tests below pin to. The printed
running-balance column on the rightmost edge of every row gives the
balance-chain ground truth.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.tidebank import TideBankExtractor
from statement_to_excel.models import RawRow

_SAMPLE = "tide_bank.pdf"

# Hand-counted from the printed transaction table (13 rows on page 1,
# 24 rows on page 2, excluding the per-page table header and the
# "Bank account legal" footer block on each page).
_EXPECTED_ROW_COUNT = 37
# Printed in the page-1 summary block ("Balance (£) on 1 Feb 2026 ...").
_OPENING_BALANCE = Decimal("56078.95")
_CLOSING_BALANCE = Decimal("1964.62")
# Printed in the page-1 summary block ("Total paid in (£) ...",
# "Total paid out (£) ...").
_TOTAL_CREDITS = Decimal("82706.70")
_TOTAL_DEBITS = Decimal("136821.03")
_FIRST_DATE = datetime.date(2026, 2, 1)
_LAST_DATE = datetime.date(2026, 4, 29)


@pytest.fixture
def tide_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Tide extractor on the sample once per test."""
    return TideBankExtractor().extract(samples_dir / _SAMPLE)


def test_tidebank_row_count(tide_rows: list[RawRow]) -> None:
    assert len(tide_rows) == _EXPECTED_ROW_COUNT


def test_tidebank_first_row_is_newest(tide_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the 29 Apr
    INDO-FUJI domestic transfer that lands on the printed closing
    balance. The description has a "/26" tail because Tide wraps the
    "Details" column across two text lines for this row — the wrapped
    tail is appended after the prefix.
    """
    first = tide_rows[0]
    assert first.date == "2026-04-29"
    assert "INDO-FUJI EUROPE LIMITED" in first.description
    assert "IFEL/INV3058/04" in first.description
    assert first.money_out == "18000.00"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_tidebank_last_row_is_oldest(tide_rows: list[RawRow]) -> None:
    """The final row in the list is the chronologically-earliest
    transaction (1 Feb 2026 DNS INFO LTD credit). Its direction can
    only be resolved by falling back to the page-1 opening-balance
    candidate, so this row exercises the
    ``_split_directions`` opening-balance seam.
    """
    last = tide_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == "DNS INFO LTD / ref: ASL-DNS-003"
    assert last.money_in == "4250.00"
    assert last.money_out == ""
    assert last.balance == "60328.95"


def test_tidebank_totals_match_summary(tide_rows: list[RawRow]) -> None:
    """Sums of money_in / money_out match the page-1 summary's
    ``Total paid in (£)`` and ``Total paid out (£)`` figures. A row
    misclassified IN-as-OUT (or vice versa) would shift both totals
    away from these values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in tide_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in tide_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_CREDITS
    assert total_out == _TOTAL_DEBITS


def test_tidebank_exactly_one_direction_per_row(
    tide_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in tide_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_tidebank_dates_within_period(tide_rows: list[RawRow]) -> None:
    for r in tide_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_tidebank_balance_chain_reconstructs_closing(
    tide_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed opening
    balance reproduces the printed closing balance, and every row's
    printed balance agrees with the running total at that point.
    A balance break would surface here before it surfaced as a "low"
    confidence flag in normalize.py.
    """
    running = _OPENING_BALANCE
    for r in reversed(tide_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_tidebank_card_transaction_wrapped_details(
    tide_rows: list[RawRow],
) -> None:
    """The 24 Apr AMAZON UK row wraps the Details column across two
    text lines printed above the date anchor ("AMAZON UK* NZ58D1DX4
    - 1 Principal Place, Worship" / "Street, LONDON"). Both prefix
    lines must be joined into the description, and the trailing
    ``Tide Card: **** **** **** 9921`` suffix line must not leak in.
    A regression in the prefix-folding logic would surface here.
    """
    matches = [
        r for r in tide_rows
        if r.date == "2026-04-24" and "AMAZON UK" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description == (
        "AMAZON UK* NZ58D1DX4 - 1 Principal Place, Worship Street, LONDON"
    )
    assert "Tide Card" not in r.description
    assert r.money_out == "10.48"
    assert r.money_in == ""


def test_tidebank_inline_fee_stripped_from_description(
    tide_rows: list[RawRow],
) -> None:
    """The 18 Apr Costco row has its ``Fee (£): 0.00`` fragment land
    on the date anchor line itself rather than on a separate suffix
    line (the Details column fit on one line, leaving room). The
    inline fee must be stripped before the tail is folded into the
    description — a regression in ``_INLINE_FEE_RE`` would surface
    here as "Fee (...)" appearing in the description.
    """
    matches = [r for r in tide_rows if "Costco" in r.description]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2026-04-18"
    assert "Fee" not in r.description
    assert r.description == (
        "Costco Online UK Limit - Hartspring Lane, Watford"
    )
    assert r.money_out == "209.99"


def test_tidebank_own_account_transfer_direction(
    tide_rows: list[RawRow],
) -> None:
    """Tide's "Own Account Transfer" type is direction-ambiguous —
    a transfer to a savings account is money_out, a transfer back is
    money_in. The 2 Apr Savings transfer (+1,000) and the 4 Feb
    Savings transfer (-56,000) exercise both directions and confirm
    the balance-differential logic resolves them correctly.
    """
    by_date = {
        ("2026-04-02", "Savings account"): ("in", "1000.00"),
        ("2026-02-04", "Savings account"): ("out", "56000.00"),
    }
    for (date, desc), (expected_dir, expected_amt) in by_date.items():
        matches = [
            r for r in tide_rows
            if r.date == date and r.description == desc
        ]
        assert len(matches) == 1, f"expected one {date} {desc!r} row"
        r = matches[0]
        if expected_dir == "in":
            assert r.money_in == expected_amt
            assert r.money_out == ""
        else:
            assert r.money_out == expected_amt
            assert r.money_in == ""


def test_tidebank_no_chrome_leaks_into_rows(
    tide_rows: list[RawRow],
) -> None:
    """The page-1 customer block (trading name, business owner,
    address, account number, sort code, opening / closing balance
    summary, "Statement for ..." period), the per-page
    "Page N of M" / "Date Transaction type ..." headers, and the
    per-page "Bank account legal" regulatory footer must not pollute
    any field. A regression in ``_filter_chrome`` would surface here.
    """
    for r in tide_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "ARCADIA" not in value
            assert "Munster Avenue" not in value
            assert "Hounslow" not in value
            assert "Trading Name" not in value
            assert "Business Owner" not in value
            assert "Account number" not in value
            assert "Sort code" not in value
            assert "Statement for" not in value
            assert "Balance (" not in value
            assert "Total paid" not in value
            assert "Page " not in value
            assert "Date Transaction type" not in value
            assert "Bank account legal" not in value
            assert "ClearBank" not in value
            assert "Financial Conduct Authority" not in value
