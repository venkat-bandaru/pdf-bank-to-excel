"""Extractor for Royal Bank of Scotland business PDF statements.

Fingerprint: page text contains "The Royal Bank of Scotland plc." —
the regulatory sentence at the start of the page footer block.

The transaction table prints five visual columns
``Date | Description | Paid In(£) | Withdrawn(£) | Balance(£)``.
Each row spans 1–2 text lines:
  * The first line may begin with a "DD MON" date (only on the first
    transaction of each calendar day; subsequent rows for the same day
    inherit the date) and carries the start of the description.
  * The terminating line ends with exactly two money tokens
    ``<amount> <balance>``. If the description fits on one line the
    terminator IS the first line; otherwise a continuation line
    carries the description tail followed by the two money tokens.

The opening balance is printed as a single
"``DD MON YYYY BROUGHT FORWARD <bal>``" row immediately after the
table header, with the year fully spelled out. We capture the year
from this row and use it to expand the year-less "DD MON" tokens that
prefix subsequent transaction rows; if the statement period crosses a
calendar year the year is incremented when the month moves backwards.

Direction (Paid In vs Withdrawn) is *not* recoverable from the linear
text alone: pdfplumber collapses the Paid-in / Withdrawn column gap
to a single space, so the row's single amount could live in either
column. We recover direction by balance-differential against the
chronologically-prior row's balance, using the BROUGHT FORWARD value
as the seed for the first transaction.

RBS prints rows oldest-first; the emitted list is reversed so that
consumers see newest-first, matching the convention that
``normalize._flag_chain_breaks`` expects.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "The Royal Bank of Scotland plc."

_MONTHS: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Leading date token: "DD MON" on a transaction row, "DD MON YYYY" on
# the BROUGHT FORWARD row. The trailing ``\s+`` requires at least one
# space after the token so a description that happens to start with
# digits followed by a 3-letter token does not accidentally match.
_LEADING_DATE_RE = re.compile(
    r"^(?P<day>\d{1,2})\s+(?P<mon>" + "|".join(_MONTHS) + r")"
    r"(?:\s+(?P<year>\d{4}))?\s+",
    re.IGNORECASE,
)

# Two trailing money tokens on a row's terminator line. Anchored to
# end-of-line so embedded "X.XX X.XX" fragments inside a description
# cannot win over the real amount/balance pair.
_TRAILING_TWO_MONEY_RE = re.compile(
    r"(?P<amount>[\d,]+\.\d{2})\s+(?P<balance>[\d,]+\.\d{2})\s*$"
)

# Table header line; opens the transaction table on every page that
# prints transactions. Parsing only collects rows after this matches
# on the current page.
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Description\s+Paid\s+In\s*\(\S\)\s+Withdrawn", re.IGNORECASE
)

# Per-page regulatory footer; everything from this line to end-of-page
# is boilerplate.
_FOOTER_MARKER = "The Royal Bank of Scotland plc."

# Literal marker for the opening-balance row.
_BROUGHT_FORWARD = "BROUGHT FORWARD"


@dataclass
class _Opening:
    """Date and balance captured from the BROUGHT FORWARD row."""

    year: int
    month: int
    balance: str


@dataclass
class _ParsedRow:
    """Intermediate parse result before direction is decided."""

    date: str         # ISO 8601
    description: str
    amount: str       # unsigned, comma-stripped
    balance: str      # unsigned, comma-stripped


class RbsBankExtractor:
    """Extracts transactions from Royal Bank of Scotland PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from an RBS statement.

        Args:
            pdf_path: Path to the RBS PDF.
            page_texts: Pre-extracted page text (the OCR path supplies
                this); if None, pdfplumber is used directly.

        Returns:
            Unvalidated raw rows, ordered newest-first to match the
            convention used by ``normalize._flag_chain_breaks``.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        table_lines, opening = _filter_chrome(page_texts)
        parsed = _parse_rows(table_lines, opening)
        log.info(
            "rbsbank: %d row(s) parsed from %s", len(parsed), pdf_path.name
        )
        return _split_directions(parsed, opening)


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _filter_chrome(
    page_texts: list[str],
) -> tuple[list[str], _Opening | None]:
    """Strip per-page chrome and return (table_lines, opening_balance).

    Each page runs a fresh state machine:
        header → table (after the first table-header line on the page) →
        chrome (after the ``The Royal Bank of Scotland plc.`` footer).

    The BROUGHT FORWARD row is the year anchor for year-less "DD MON"
    tokens that prefix transaction rows, and supplies the previous
    balance for the chronologically-first transaction's direction
    check; both pieces of state are returned alongside the table lines.
    """
    kept: list[str] = []
    opening: _Opening | None = None
    for text in page_texts:
        in_table = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_FOOTER_MARKER):
                break  # rest of this page is regulatory boilerplate
            if not in_table:
                if _TABLE_HEADER_RE.match(line):
                    in_table = True
                continue
            # in_table: drop repeated table-header on subsequent pages.
            if _TABLE_HEADER_RE.match(line):
                continue
            if _BROUGHT_FORWARD in line:
                if opening is None:
                    opening = _parse_brought_forward(line)
                continue
            kept.append(line)
    return kept, opening


def _parse_brought_forward(line: str) -> _Opening | None:
    """Pull (year, month, balance) from a ``DD MON YYYY BROUGHT FORWARD <bal>``
    line.
    """
    m = _LEADING_DATE_RE.match(line)
    if m is None or m.group("year") is None:
        return None
    bal_match = re.search(r"([\d,]+\.\d{2})\s*$", line)
    if bal_match is None:
        return None
    return _Opening(
        year=int(m.group("year")),
        month=_MONTHS[m.group("mon").upper()],
        balance=bal_match.group(1).replace(",", ""),
    )


def _parse_rows(
    lines: list[str], opening: _Opening | None
) -> list[_ParsedRow]:
    """Accumulate lines into rows; terminate on a two-money trailing line.

    Tracks the current calendar date across rows: a row's first line
    may begin with "DD MON" (start of a new day's first transaction)
    or omit the date (continuation of the previous day). The year is
    inferred from the opening BROUGHT FORWARD row and incremented when
    the month moves backwards (statement period crossing year-end).
    """
    rows: list[_ParsedRow] = []
    buffer: list[str] = []
    current_year = opening.year if opening else datetime.date.today().year
    current_month = opening.month if opening else 0
    current_date_iso: str | None = None

    for raw_line in lines:
        line = raw_line
        date_match = _LEADING_DATE_RE.match(line)
        # Only strip year-less dates: a year-bearing date would be the
        # BROUGHT FORWARD row, which is filtered out upstream.
        if date_match and date_match.group("year") is None:
            day = int(date_match.group("day"))
            month = _MONTHS[date_match.group("mon").upper()]
            if current_month and month < current_month:
                current_year += 1
            current_month = month
            current_date_iso = datetime.date(
                current_year, month, day
            ).isoformat()
            line = line[date_match.end():].strip()
            if not line:
                continue

        buffer.append(line)
        money_match = _TRAILING_TWO_MONEY_RE.search(buffer[-1])
        if money_match is None:
            continue
        amount = money_match.group("amount").replace(",", "")
        balance = money_match.group("balance").replace(",", "")
        buffer[-1] = buffer[-1][: money_match.start()].strip()
        description = " ".join(seg for seg in buffer if seg).strip()
        if current_date_iso is None:
            log.warning(
                "rbsbank: row with no leading date in scope: %r",
                description,
            )
        rows.append(
            _ParsedRow(
                date=current_date_iso or "",
                description=description,
                amount=amount,
                balance=balance,
            )
        )
        buffer = []

    if buffer:
        log.warning(
            "rbsbank: %d leftover line(s) without a terminator: %r",
            len(buffer), buffer,
        )
    return rows


def _split_directions(
    rows: list[_ParsedRow], opening: _Opening | None
) -> list[RawRow]:
    """Decide money_in vs money_out by balance differential.

    Rows are processed in print order (oldest-first); the previous
    balance starts at the BROUGHT FORWARD value and rolls forward with
    each emitted row. After direction is decided the list is reversed
    so the returned RawRows are newest-first, matching the convention
    used by ``normalize._flag_chain_breaks``.
    """
    out: list[RawRow] = []
    prev_balance = opening.balance if opening else None
    for row in rows:
        direction = (
            _direction_from_delta(prev_balance, row)
            if prev_balance is not None
            else None
        )
        if direction is None:
            log.warning(
                "rbsbank: could not determine direction for %r; "
                "defaulting to money_out",
                row,
            )
            direction = "out"
        money_in = row.amount if direction == "in" else ""
        money_out = row.amount if direction == "out" else ""
        out.append(
            RawRow(
                date=row.date,
                description=row.description,
                money_out=money_out,
                money_in=money_in,
                balance=row.balance,
            )
        )
        prev_balance = row.balance
    out.reverse()
    return out


def _direction_from_delta(
    prev_balance: str, row: _ParsedRow
) -> str | None:
    """Return "in" / "out" / None from a row.balance vs prev_balance diff."""
    try:
        delta = Decimal(row.balance) - Decimal(prev_balance)
        amt = Decimal(row.amount)
    except InvalidOperation:
        return None
    tolerance = Decimal("0.01")
    if abs(delta - amt) <= tolerance:
        return "in"
    if abs(delta + amt) <= tolerance:
        return "out"
    return None
