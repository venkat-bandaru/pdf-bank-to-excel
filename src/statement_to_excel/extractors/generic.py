"""Fallback extractor for unrecognised bank statement layouts.

Used when no fingerprint in detect.py matches the PDF. Applies heuristics
(e.g. look for a row that parses as a date followed by a number) to make a
best-effort extraction. Expected to fail on unusual layouts; failures are
surfaced by the balance-chain check in normalize.py rather than crashing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

# A row line looks like:
#   2026-01-30 IW Group Services UK FPO 117.48 656.65
# i.e. an ISO date, free-form description (which may include a type code),
# then two money amounts: the txn amount and the running balance.
_ROW_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+"
    r"(?P<amount>-?[\d,]+\.\d{2})\s+(?P<balance>-?[\d,]+\.\d{2})\s*$"
)
_PAGE_TIMESTAMP_RE = re.compile(r"^\d{2}/\d{2}/\d{4},\s+\d{2}:\d{2}\s*$")
_FOOTER_RE = re.compile(r"https?://|Page\s+\d+\s+of\s+\d+", re.IGNORECASE)
_TABLE_HEADER_RE = re.compile(r"^Date\s+Description", re.IGNORECASE)
# Markers for the legend / boilerplate that follows the data table on the
# final page. Anything from here on is not transaction data.
_LEGEND_MARKERS = (
    "Cash machine",          # TSB legend opener: "ATM Cash machine ..."
    "Registered office",
    "Authorised by",
    "Financial Conduct Authority",
)

# Type codes used to disambiguate direction when there is no previous balance
# to diff against (i.e. the chronologically-first row in the statement).
# The balance-differential path handles every other row.
_TYPE_OUT = frozenset({"DEB", "DD", "CHG", "CHQ", "SO", "ATM", "FPO", "BP",
                       "COMM", "COR", "MTU", "PAY", "PSV", "EUR"})
_TYPE_IN = frozenset({"DEP", "FPI", "SAL", "BGC", "CR"})


@dataclass
class _ParsedRow:
    """Intermediate parse result before money_in/money_out split."""

    date: str
    desc: str
    amount: str
    balance: str


class GenericExtractor:
    """Best-effort heuristic extractor for unknown bank layouts."""

    def extract(self, pdf_path: Path, page_texts: list[str] | None = None) -> list[RawRow]:
        """Attempt to extract transactions using layout-agnostic heuristics.

        Args:
            pdf_path: Path to the PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows. May be incomplete or empty for unusual layouts.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)

        lines = _filter_chrome(page_texts)
        parsed = _parse_rows(lines)
        log.info("generic: %d row(s) parsed from %s", len(parsed), pdf_path.name)
        return _split_directions(parsed)


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _filter_chrome(page_texts: list[str]) -> list[str]:
    """Strip per-page header/footer noise and table column headers.

    Keeps everything else (including continuation lines for multi-line
    descriptions) so the row parser can fold them into the previous row.
    """
    out: list[str] = []
    for text in page_texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _PAGE_TIMESTAMP_RE.match(line):
                continue
            if _FOOTER_RE.search(line):
                continue
            if _TABLE_HEADER_RE.match(line):
                continue
            out.append(line)
    return out


def _parse_rows(lines: list[str]) -> list[_ParsedRow]:
    """Walk the cleaned lines, recognising new rows by the leading date.

    Lines that do not start with a date are treated as continuations of the
    previous row's description (this is how multi-line descriptions like
    "FROM Business Instant\\nAccess 779154-\\n81628960" survive).
    """
    rows: list[_ParsedRow] = []
    current: _ParsedRow | None = None
    for line in lines:
        if any(marker in line for marker in _LEGEND_MARKERS):
            break
        match = _ROW_RE.match(line)
        if match:
            if current is not None:
                rows.append(current)
            current = _ParsedRow(
                date=match["date"],
                desc=match["desc"].strip(),
                amount=match["amount"],
                balance=match["balance"],
            )
        elif current is not None:
            current.desc = f"{current.desc} {line}".strip()
    if current is not None:
        rows.append(current)
    return rows


def _split_directions(rows: list[_ParsedRow]) -> list[RawRow]:
    """Decide whether each row's amount is money_in or money_out.

    The PDF prints transactions newest-first; the row immediately below in
    print is the chronologically-prior transaction, so its balance is the
    "previous balance" for the row above. The signed delta of the two balances
    must equal +amount (money in) or -amount (money out). For the very last
    printed row (chronologically first) there is no previous balance, so we
    fall back to a type-code lookup in the description.
    """
    out: list[RawRow] = []
    for i, row in enumerate(rows):
        direction = _direction_from_balance(rows, i) or _direction_from_type(row.desc)
        if direction is None:
            log.warning("Could not determine direction for row %r; defaulting to money_out", row)
            direction = "out"
        money_in = row.amount if direction == "in" else ""
        money_out = row.amount if direction == "out" else ""
        out.append(
            RawRow(
                date=row.date,
                description=row.desc,
                money_out=money_out,
                money_in=money_in,
                balance=row.balance,
            )
        )
    return out


def _direction_from_balance(rows: list[_ParsedRow], i: int) -> str | None:
    """Compare row i's balance against the next-printed row's balance."""
    if i + 1 >= len(rows):
        return None
    try:
        cur = _money(rows[i].balance)
        prev = _money(rows[i + 1].balance)
        amt = _money(rows[i].amount)
    except InvalidOperation:
        return None
    delta = cur - prev
    tolerance = Decimal("0.01")
    if abs(delta - amt) <= tolerance:
        return "in"
    if abs(delta + amt) <= tolerance:
        return "out"
    return None


def _direction_from_type(desc: str) -> str | None:
    """Look for a known type-code token in the description."""
    for token in desc.split():
        upper = token.strip(".,:;").upper()
        if upper in _TYPE_OUT:
            return "out"
        if upper in _TYPE_IN:
            return "in"
    return None


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))
