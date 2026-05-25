"""Tests for the Barclays 2026 PDF statement extractor.

Sample under test: ``tests/samples/barclays_new.pdf`` — a five-page
Barclays Business Account statement in the new online-banking
layout, covering 02 Jan 2026 through 12 May 2026. The page-1 banner
prints the row count ("Showing 57 transactions ...") that the tests
below pin to; the printed running-balance column on the rightmost
edge of every row gives the balance-chain ground truth.

The legacy "At a glance sidebar" Barclays layout is handled by the
sibling ``barclays.py`` extractor and its own test file. The two
formats share the "Barclays Bank UK PLC" footer string and are
distinguished by the page-1 banner ``Last night's balance`` (new
only) — see ``detect.py`` for the dispatch order.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.barclays_2026 import Barclays2026Extractor
from statement_to_excel.models import RawRow

_SAMPLE = "barclays_new.pdf"

# Printed on the page-1 banner ("Showing 57 transactions ...").
_EXPECTED_ROW_COUNT = 57
# Derived: the last (chronologically-first) row in the sample is the
# 02 Jan HMRC VAT row whose post-transaction balance is £2,376.40
# and money_out is £1,769.00. Working back: opening = 2376.40 +
# 1769.00 = 4145.40.
_OPENING_BALANCE = Decimal("4145.40")
# Printed in the page-1 summary block ("Available balance £62.89").
_CLOSING_BALANCE = Decimal("62.89")
# Hand-summed from the signed amount column.
_TOTAL_CREDITS = Decimal("40982.40")
_TOTAL_DEBITS = Decimal("45064.91")
_FIRST_DATE = datetime.date(2026, 1, 2)
_LAST_DATE = datetime.date(2026, 5, 12)


@pytest.fixture
def barclays_rows(samples_dir: Path) -> list[RawRow]:
    """Run the 2026 Barclays extractor on the sample once per test."""
    return Barclays2026Extractor().extract(samples_dir / _SAMPLE)


def test_barclays_2026_row_count(barclays_rows: list[RawRow]) -> None:
    """The page-1 banner prints "Showing 57 transactions ..."; a
    regression in the money-line anchor logic would either skip rows
    or split one into two."""
    assert len(barclays_rows) == _EXPECTED_ROW_COUNT


def test_barclays_2026_first_row_is_newest(
    barclays_rows: list[RawRow],
) -> None:
    """Rows are emitted newest-first to match the print order; the
    first row is the 12 May Standing Order to GLOBAL ACCOUNTANTS
    that lands on the printed closing balance.
    """
    first = barclays_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description.startswith("Standing Order")
    assert "GLOBAL ACCOUNTANTS" in first.description
    assert first.money_out == "75.00"
    assert first.money_in == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_barclays_2026_last_row_is_oldest(
    barclays_rows: list[RawRow],
) -> None:
    """The final row in the list is the chronologically-earliest
    transaction (02 Jan HMRC VAT bill payment). Its date is built
    from a "DD/MM" line ("02/01") and a "/YYYY" tail line ("/2026")
    captured separately by the parser.
    """
    last = barclays_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert "HMRC VAT" in last.description
    assert last.money_out == "1769.00"
    assert last.money_in == ""
    assert last.balance == "2376.40"


def test_barclays_2026_totals_match_signed_sum(
    barclays_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match hand-summed totals over
    the signed amount column. A row whose sign was flipped during
    the ``_to_raw_row`` split would shift both totals away from
    these values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in barclays_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in barclays_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_CREDITS
    assert total_out == _TOTAL_DEBITS


def test_barclays_2026_exactly_one_direction_per_row(
    barclays_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in barclays_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_barclays_2026_dates_within_period(
    barclays_rows: list[RawRow],
) -> None:
    """Every row's date sits inside the period bounds printed in the
    "Showing ... transactions between DD/MM/YYYY and DD/MM/YYYY"
    banner — a regression in the year-tail combine logic would land
    rows on the wrong year.
    """
    for r in barclays_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_barclays_2026_balance_chain_reconstructs_closing(
    barclays_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the derived opening
    balance reproduces the printed closing balance, and every row's
    printed balance agrees with the running total at that point. A
    balance break would surface here before it surfaced as a "low"
    confidence flag in normalize.py.
    """
    running = _OPENING_BALANCE
    for r in reversed(barclays_rows):  # reversed -> chronological order
        amount = (
            Decimal(r.money_in) if r.money_in else -Decimal(r.money_out)
        )
        running += amount
        assert Decimal(r.balance) == running, (
            f"balance chain broke at {r.date} {r.description!r}: "
            f"running={running}, printed balance={r.balance}"
        )
    assert running == _CLOSING_BALANCE


def test_barclays_2026_negative_balance_preserved(
    barclays_rows: list[RawRow],
) -> None:
    """The account dips into overdraft on 3-4 Feb (-£585.50 and
    -£594.00). The leading minus must survive ``_strip_money``;
    a regression that dropped the sign would land these as positive
    balances and silently break the chain check on the row below.
    """
    matches = {
        r.date: r.balance
        for r in barclays_rows
        if r.date in {"2026-02-03", "2026-02-04"}
        and "BARCLAYS" in r.description or
        ("CHARGES" in r.description and r.date == "2026-02-04")
    }
    assert "-585.50" in matches.values()
    assert "-594.00" in matches.values()


def test_barclays_2026_inline_type_anomaly(
    barclays_rows: list[RawRow],
) -> None:
    """The 16 Mar S JAMPANI row prints the type label inline with
    the date ("16/03 Bill Payment -£2,000.00 £7,107.10") rather
    than on its own line above. A regression in the inline-date
    branch of the money-line handler would split this row in two
    or drop its description.
    """
    matches = [
        r for r in barclays_rows
        if r.date == "2026-03-16" and "DIVIDEND" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert "Bill Payment" in r.description
    assert "S JAMPANI" in r.description
    assert r.money_out == "2000.00"
    assert r.balance == "7107.10"


def test_barclays_2026_multipage_row_continuation(
    barclays_rows: list[RawRow],
) -> None:
    """The 29 Apr Counter Credit straddles the page-1 / page-2
    boundary: the "Counter Credit" type label is the last line of
    page 1, the money line and the rest of the row are on page 2.
    A regression in ``_filter_chrome``'s cross-page ``in_table``
    state would drop the row's type label or drop the row entirely.
    """
    matches = [
        r for r in barclays_rows
        if r.date == "2026-04-29" and "LOAN PAID BACK" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description.startswith("Counter Credit")
    assert r.money_in == "1500.00"
    assert r.balance == "8895.10"


def test_barclays_2026_cross_page_description_tail(
    barclays_rows: list[RawRow],
) -> None:
    """The 08 Jan CHARGES row's description trails into page 5
    ("13NOV/14DEC ********** ************" is on page 5 even though
    the money line is on page 4). The "between" state must persist
    across the page boundary so the trailing fragment attaches to
    the right row.
    """
    matches = [
        r for r in barclays_rows
        if r.date == "2026-01-08" and "CHARGES" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert "13NOV/14DEC" in r.description
    assert r.money_out == "8.50"
    assert r.balance == "157.98"


def test_barclays_2026_direction_in_for_positive_amount(
    barclays_rows: list[RawRow],
) -> None:
    """An amount printed without a leading minus is money in. The
    17 Apr CREATIVITY credit (£9,319.20, the largest single inbound
    transfer in the sample) exercises both the positive-sign branch
    of ``_to_raw_row`` and the comma-stripping in ``_strip_money``.
    """
    matches = [
        r for r in barclays_rows
        if r.date == "2026-04-17" and "CREATIVITY" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_in == "9319.20"
    assert r.money_out == ""
    assert r.balance == "9475.22"


def test_barclays_2026_no_chrome_leaks_into_rows(
    barclays_rows: list[RawRow],
) -> None:
    """The page-1 summary block (customer name, account number,
    sort code, "Today: ...", available/last-night/overdraft balances,
    "Showing N transactions ..." banner), the per-page "Page N of M"
    footers, and the end-of-table regulatory boilerplate must not
    pollute any field. A regression in ``_filter_chrome`` would
    surface here.
    """
    for r in barclays_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "NKS SOFTWARE" not in value
            assert "Today:" not in value
            assert "Available balance" not in value
            assert "Last night's balance" not in value
            assert "Overdraft limit" not in value
            assert "Showing " not in value
            assert "transactions between" not in value
            assert "Page " not in value
            assert "Need to view older" not in value
            assert "Barclays Bank UK PLC" not in value
            assert "Financial Conduct Authority" not in value


def test_barclays_2026_credit_payment_charges_multiline_desc(
    barclays_rows: list[RawRow],
) -> None:
    """The "Credit Payment CHARGES" row format spreads description
    across 4 distinct fragments — the type label, "CHARGES" on its
    own line, "COMMISSION FOR PERIOD" appearing after the /YYYY
    year tail, and a final masked-PAN line. All four must join into
    one description without any of them slipping into the next
    row's prelude.
    """
    matches = [
        r for r in barclays_rows
        if r.date == "2026-05-05" and "COMMISSION FOR PERIOD" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.description.startswith("Credit Payment")
    assert "CHARGES" in r.description
    assert "COMMISSION FOR PERIOD" in r.description
    assert "13MAR/12APR" in r.description
    assert r.money_out == "8.50"
