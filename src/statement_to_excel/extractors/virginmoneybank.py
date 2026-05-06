"""Extractor for Virgin Money business PDF statements.

Fingerprint: page text contains "Virgin Money" — the brand name appears
in the "Interest rate information" boilerplate that follows the
transaction table on the final page.

The transaction table prints six visual columns
``Date | Description | Type | Debits | Credits | Balance``. pdfplumber's
text-mode extraction collapses adjacent empty columns into a single
space, so the Debits column on a credit row and the Credits column
on a debit row are both invisible in the extracted text — every row,
regardless of direction, surfaces with exactly two money tokens at
the end of its anchor line. Description-internal numbers (reference
codes, account fragments like ``1.90 0.02``) carry no currency
prefix, so the two prefixed money tokens at the right of an anchor
line are unambiguous.

The currency prefix is the literal ``£`` in some PDFs but appears as
``\\uFFFD`` (the Unicode replacement character) when the font's CMap
fails to map the £ glyph to U+00A3 — both forms are accepted.

Multi-line descriptions are the awkward case. When the description
cell wraps to two visual rows, pdfplumber emits the row as three
text lines:

    <description top fragment>
    <DD MMM YY> <type> <amount> <balance>
    <description bottom fragment>

i.e. the date / type / amount / balance line is sandwiched between
the two halves of the description. We detect this layout by spotting
anchor lines whose immediate neighbours (above and below) are both
non-anchor text fragments, and fold both fragments into the anchor's
description. Single-line rows have anchor neighbours on both sides
and are taken as-is.

Direction (debit vs credit) is recovered by signed balance differential
against the previous row — the column the printed amount actually
lived in is not recoverable from text alone. Virgin Money prints
oldest-first, so each row's "previous balance" is the row immediately
above it (or the printed Opening Balance figure for the first
transaction). This is the same approach used by ``generic.py``.

Rows are emitted newest-first to match the convention used by the
other extractors and ``normalize._flag_chain_breaks``; the result of
the oldest-first parse is therefore reversed before returning.
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

FINGERPRINT = "Virgin Money"

_MONTHS: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# The currency prefix is "£" or, if the font CMap is missing, U+FFFD.
_POUND = r"[£�]"
# A Virgin Money date token: two-digit day, three-letter month, two-digit year.
_DATE_PREFIX_RE = re.compile(r"^\d{2}\s+[A-Za-z]{3}\s+\d{2}\b")
# A complete transaction "anchor" row:
#   DD MMM YY <description and/or type> £<amount> £<balance>
# The lazy ``desc`` group ends just before the first money token,
# leaving exactly the two trailing tokens to bind to amount and balance.
_ROW_RE = re.compile(
    r"^(?P<date>\d{2}\s+[A-Za-z]{3}\s+\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    + _POUND + r"(?P<amount>-?[\d,]+\.\d{2})\s+"
    + _POUND + r"(?P<balance>-?[\d,]+\.\d{2})\s*$"
)
# The opening-balance row carries only a balance and is not a transaction.
_OPENING_RE = re.compile(
    r"^\d{2}\s+[A-Za-z]{3}\s+\d{2}\s+Opening\s+Balance\s+"
    + _POUND + r"(?P<balance>-?[\d,]+\.\d{2})\s*$"
)
# The "Date Description Type Debits Credits Balance" line that opens
# the table. Lines preceding it on page 1 (customer name, account
# number, "01 Jan 25 - 31 Jan 25" statement period) must not be parsed.
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Description\s+Type\s+Debits\s+Credits\s+Balance",
    re.IGNORECASE,
)
# Markers for the totals / legend block that follows the data table on
# the final page. Anything from here on is not transaction data.
_LEGEND_MARKERS: tuple[str, ...] = (
    "Total debits",
    "Total credits",
    "Closing Balance",
    "Important information",
)


@dataclass
class _ParsedRow:
    """Intermediate parse result before money_in/money_out direction is set."""

    date: str
    description: str
    amount: str
    balance: str


class VirginMoneyBankExtractor:
    """Extracts transactions from Virgin Money PDF business statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Virgin Money statement.

        Args:
            pdf_path: Path to the Virgin Money PDF.
            page_texts: Pre-extracted page text (the OCR path supplies
                this); if None, pdfplumber is used directly.

        Returns:
            Unvalidated raw rows, ordered newest-first. Virgin Money
            prints oldest-first, so the result is reversed before
            returning.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        lines = _filter_chrome(page_texts)
        opening_balance = _opening_balance(lines)
        parsed = _parse_rows(lines)
        log.info(
            "virginmoneybank: %d row(s) parsed from %s",
            len(parsed),
            pdf_path.name,
        )
        rows = _split_directions(parsed, opening_balance)
        rows.reverse()
        return rows


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _filter_chrome(page_texts: list[str]) -> list[str]:
    """Skip per-page chrome; keep only lines from the transaction table.

    The customer-info block on page 1 contains a "01 Jan 25 - 31 Jan 25"
    statement-period line that would otherwise be picked up by the
    date-prefix regex. Starting collection only after the table header
    row sidesteps that. Collection stops at the totals/legend block on
    the final page. A repeated table header on a later page (if the
    sample ever ships one) is also skipped.
    """
    out: list[str] = []
    in_table = False
    for text in page_texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if any(line.startswith(marker) for marker in _LEGEND_MARKERS):
                return out
            if _TABLE_HEADER_RE.match(line):
                in_table = True
                continue
            if not in_table:
                continue
            out.append(line)
    return out


def _opening_balance(lines: list[str]) -> str | None:
    """Return the printed opening balance, if the table begins with one.

    The opening-balance row is excluded from the transaction list (it
    has no transaction amount), but the figure is needed to recover
    direction for the very first transaction via balance differential.
    """
    for line in lines:
        match = _OPENING_RE.match(line)
        if match is not None:
            return match.group("balance").replace(",", "")
        if _DATE_PREFIX_RE.match(line):
            # First date-prefixed line was not an opening balance row;
            # the table has no printed opening balance.
            return None
    return None


def _parse_rows(lines: list[str]) -> list[_ParsedRow]:
    """Walk the kept lines, folding multi-line descriptions into anchors.

    Each anchor line (one that matches ``_ROW_RE``) is one transaction.
    A multi-line description shows up as ``text \\n anchor \\n text``:
    a non-anchor fragment immediately above AND immediately below the
    anchor row. Both fragments are folded into that anchor's
    description in reading order (top, bottom, then the type word that
    sits inside the anchor itself). Single-line rows have anchor
    neighbours and are taken as-is.

    Lines that fail to match — most notably the opening-balance row,
    which carries only one money token — are silently dropped.
    """
    matches = [_ROW_RE.match(line) for line in lines]
    is_anchor = [m is not None for m in matches]
    rows: list[_ParsedRow] = []
    consumed = [False] * len(lines)
    for i, match in enumerate(matches):
        if match is None or consumed[i]:
            continue
        desc_anchor = match.group("desc").strip()
        above = (
            lines[i - 1].strip()
            if i > 0 and not is_anchor[i - 1] and not consumed[i - 1]
            else None
        )
        below = (
            lines[i + 1].strip()
            if i + 1 < len(lines)
            and not is_anchor[i + 1]
            and not consumed[i + 1]
            else None
        )
        if above is not None and below is not None:
            description = f"{above} {below} {desc_anchor}".strip()
            consumed[i - 1] = True
            consumed[i + 1] = True
        else:
            description = desc_anchor
        rows.append(
            _ParsedRow(
                date=_to_iso(match.group("date")),
                description=description,
                amount=match.group("amount").replace(",", ""),
                balance=match.group("balance").replace(",", ""),
            )
        )
    return rows


def _split_directions(
    rows: list[_ParsedRow], opening_balance: str | None
) -> list[RawRow]:
    """Decide whether each row's amount is money_out or money_in.

    Virgin Money prints oldest-first, so each row's previous balance
    is the row immediately above it (or the printed opening balance
    for row 0). A signed delta equal to ``+amount`` is money_in, equal
    to ``-amount`` is money_out. If the differential is inconclusive
    (an unparseable or missing prior balance), default to money_out
    and warn — the balance-chain check in normalize.py will flag the
    row at "low" confidence.
    """
    out: list[RawRow] = []
    prev_balance: Decimal | None = (
        _money(opening_balance) if opening_balance is not None else None
    )
    tolerance = Decimal("0.01")
    for row in rows:
        try:
            cur = _money(row.balance)
            amt = _money(row.amount)
        except InvalidOperation:
            log.warning(
                "virginmoneybank: unparseable money in row %r; defaulting to money_out",
                row,
            )
            out.append(
                RawRow(
                    date=row.date,
                    description=row.description,
                    money_out=row.amount,
                    money_in="",
                    balance=row.balance,
                )
            )
            prev_balance = None
            continue
        if prev_balance is None:
            log.warning(
                "virginmoneybank: no previous balance for row %r; defaulting to money_out",
                row,
            )
            direction = "out"
        else:
            delta = cur - prev_balance
            if abs(delta - amt) <= tolerance:
                direction = "in"
            elif abs(delta + amt) <= tolerance:
                direction = "out"
            else:
                log.warning(
                    "virginmoneybank: balance differential inconclusive for row %r; defaulting to money_out",
                    row,
                )
                direction = "out"
        out.append(
            RawRow(
                date=row.date,
                description=row.description,
                money_out=row.amount if direction == "out" else "",
                money_in=row.amount if direction == "in" else "",
                balance=row.balance,
            )
        )
        prev_balance = cur
    return out


def _to_iso(date_str: str) -> str:
    """Convert a "DD MMM YY" date token to ISO 8601, assuming 20YY."""
    day_s, month_s, year_s = date_str.split()
    month = _MONTHS[month_s.title()]
    year = 2000 + int(year_s)
    return datetime.date(year, month, int(day_s)).isoformat()


def _money(s: str) -> Decimal:
    """Parse a comma-stripped money string as a Decimal."""
    return Decimal(s.replace(",", ""))
