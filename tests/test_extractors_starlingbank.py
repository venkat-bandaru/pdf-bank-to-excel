"""Tests for the Starling Bank PDF statement extractor.

Sample under test: ``tests/samples/StarlingStatement_2025-10-01_2025-12-31.pdf``
— a four-page Starling Bank business statement covering 1 October 2025
through 31 December 2025. The printed "Summary" block on page 1 gives
the ground-truth opening / closing balance and direction totals the
tests below pin to.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from statement_to_excel.extractors.starlingbank import StarlingBankExtractor
from statement_to_excel.models import RawRow

_SAMPLE = "StarlingStatement_2025-10-01_2025-12-31.pdf"

# Hand-counted from the four pages of the sample (188 transaction rows;
# the "OPENING BALANCE" row carries a balance only and is not a
# transaction, the "01/10/2025 - 31/12/2025 Statement" page-header
# line carries no money tokens — both are correctly excluded).
_EXPECTED_ROW_COUNT = 188
# Printed "Summary" block on page 1.
_OPENING_BALANCE = Decimal("18739.04")
_CLOSING_BALANCE = Decimal("37189.20")
_TOTAL_PAYMENTS_IN = Decimal("96026.08")
_TOTAL_PAYMENTS_OUT = Decimal("77575.92")
_FIRST_DATE = datetime.date(2025, 10, 1)
_LAST_DATE = datetime.date(2025, 12, 31)


@pytest.fixture
def starling_rows(samples_dir: Path) -> list[RawRow]:
    """Run the Starling extractor on the sample once per test."""
    return StarlingBankExtractor().extract(samples_dir / _SAMPLE)


def test_starlingbank_row_count(starling_rows: list[RawRow]) -> None:
    assert len(starling_rows) == _EXPECTED_ROW_COUNT


def test_starlingbank_first_row_is_newest(starling_rows: list[RawRow]) -> None:
    """Rows are emitted newest-first; the first row is the 31/12 TEYA
    FASTER PAYMENT IN whose printed balance equals the closing balance."""
    first = starling_rows[0]
    assert first.date == _LAST_DATE.isoformat()
    assert first.description == (
        "FASTER PAYMENT TEYA SOLUTIONS LTD (5065970TEYA251231)"
    )
    assert first.money_in == "648.77"
    assert first.money_out == ""
    assert first.balance == str(_CLOSING_BALANCE)


def test_starlingbank_last_row_is_oldest(starling_rows: list[RawRow]) -> None:
    """The final row is the chronologically-earliest transaction —
    01/10 DIRECT CREDIT Uber Payments. It is NOT the last row of its
    day, so it carries no end-of-day balance.
    """
    last = starling_rows[-1]
    assert last.date == _FIRST_DATE.isoformat()
    assert last.description == (
        "DIRECT CREDIT Uber Payments Uk L (YHMGX4NYZMUY)"
    )
    assert last.money_in == "3218.98"
    assert last.money_out == ""
    assert last.balance == ""


def test_starlingbank_totals_match_summary(
    starling_rows: list[RawRow],
) -> None:
    """Sums of money_in / money_out match the printed Payments In /
    Payments Out totals on page 1. This is the strongest single proof
    that x-position-based direction classification is right — a row
    misclassified as IN that should be OUT would shift both totals
    away from these printed values.
    """
    total_in = sum(
        (Decimal(r.money_in) for r in starling_rows if r.money_in),
        Decimal("0"),
    )
    total_out = sum(
        (Decimal(r.money_out) for r in starling_rows if r.money_out),
        Decimal("0"),
    )
    assert total_in == _TOTAL_PAYMENTS_IN
    assert total_out == _TOTAL_PAYMENTS_OUT


def test_starlingbank_exactly_one_direction_per_row(
    starling_rows: list[RawRow],
) -> None:
    """Every row sets exactly one of money_out / money_in."""
    for r in starling_rows:
        assert (r.money_out == "") != (r.money_in == "")


def test_starlingbank_dates_within_period(
    starling_rows: list[RawRow],
) -> None:
    for r in starling_rows:
        d = datetime.date.fromisoformat(r.date)
        assert _FIRST_DATE <= d <= _LAST_DATE


def test_starlingbank_faster_payment_direction_is_x_classified(
    starling_rows: list[RawRow],
) -> None:
    """``FASTER PAYMENT`` is direction-blind in text mode — Roofoods
    (Deliveroo paying us) and Krupa Sindhu Baral (us paying them) both
    print as ``FASTER PAYMENT`` on the same statement. The only
    discriminator is x-position (IN column vs OUT column). A regression
    in the classifier would surface here.
    """
    roofoods_in = next(
        r for r in starling_rows
        if r.date == "2025-10-02" and "Roofoods" in r.description
    )
    assert roofoods_in.money_in == "713.57"
    assert roofoods_in.money_out == ""

    krupa_out = next(
        r for r in starling_rows
        if r.date == "2025-10-04" and "Krupa Sindhu Baral" in r.description
    )
    assert krupa_out.money_out == "100.00"
    assert krupa_out.money_in == ""


def test_starlingbank_chip_and_pin_multi_word_type_preserved(
    starling_rows: list[RawRow],
) -> None:
    """``CHIP & PIN`` is a three-token type code. The description must
    preserve all three tokens, in order, in front of the merchant text.
    A regression that picked up only the first word would surface as
    e.g. ``CHIP Costco Wholesale`` here.
    """
    costco = next(
        r for r in starling_rows
        if "Costco Wholesale" in r.description
    )
    assert costco.description == "CHIP & PIN Costco Wholesale #106"
    assert costco.money_out == "242.58"


def test_starlingbank_continuation_folded(
    starling_rows: list[RawRow],
) -> None:
    """The 14/12 Perfect Takeaway Packs row wraps its reference
    suffix ``inv-0462)`` onto a continuation line below the date row.
    The continuation must fold into the description, and the page-
    break header on page 4 must NOT (that would surface as an extra
    ``24hr Customer Service`` token in some later-page row).
    """
    matches = [
        r for r in starling_rows if "Perfect Takeaway" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.date == "2025-12-14"
    assert r.description == (
        "FASTER PAYMENT Perfect Takeaway Packs UK Limited "
        "(faheems inv-0462)"
    )
    assert r.money_out == "1200.00"
    assert r.balance == "25090.05"


def test_starlingbank_balance_chain_reconstructs_closing(
    starling_rows: list[RawRow],
) -> None:
    """Walking the rows chronologically from the printed opening
    balance reproduces the printed closing balance, and every row that
    carries an end-of-day balance agrees with the running total at
    that point. Most rows do not carry a balance (Starling only prints
    one on the last row of each calendar day), so the inner check is
    gated on r.balance being set.
    """
    running = _OPENING_BALANCE
    for r in reversed(starling_rows):  # reversed -> chronological order
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


def test_starlingbank_no_chrome_leaks_into_rows(
    starling_rows: list[RawRow],
) -> None:
    """Per-page chrome (the "24hr Customer Service" / "www.starlingbank.com"
    page header, the regulatory footer that opens with ``Starling Bank
    is registered``, the page number, the trailing interest-rate
    disclosure block on page 4) and page-1 summary text must not
    pollute any field.
    """
    for r in starling_rows:
        for value in (r.date, r.description, r.money_out, r.money_in, r.balance):
            assert "Starling Bank" not in value
            assert "24hr Customer" not in value
            assert "www.starlingbank.com" not in value
            assert "OPENING BALANCE" not in value
            assert "Closing Balance" not in value
            assert "Date range applicable" not in value
            assert "Payments In" not in value
            assert "Payments Out" not in value


def test_starlingbank_multipage_first_row_on_page_two(
    starling_rows: list[RawRow],
) -> None:
    """The first row on page 2 (23/10 Roofoods, IN £1024.74) must be
    present and correctly classified — confirms the page-1 / page-2
    boundary doesn't drop the row and that x-position direction
    classification keeps working when the active row resets at the
    page break.
    """
    matches = [
        r for r in starling_rows
        if r.date == "2025-10-23" and "Roofoods" in r.description
    ]
    assert len(matches) == 1
    r = matches[0]
    assert r.money_in == "1024.74"
    assert r.money_out == ""
