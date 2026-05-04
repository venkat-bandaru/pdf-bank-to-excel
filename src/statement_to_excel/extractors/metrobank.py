"""Extractor for Metro Bank UK business bank statements.

Fingerprint: page text contains "metrobank".

Each transaction *opens* with a line carrying (in order) a "DD MMM YYYY"
date prefix, the first chunk of the description, the transaction amount,
and the running balance. Subsequent lines without trailing money tokens
are description continuations until the next opener. The amount column
under the printed header is "Money out" or "Money in" depending on the
transaction; pdfplumber collapses the empty column away, so we recover
direction (money in vs money out) by comparing each row's balance
against the prior row's balance, anchored at the start by the printed
"Balance brought forward" line.

Some Metrobank PDFs ship with a stray byte (typically a newline) before
the "%PDF-" magic; pdfplumber's parser silently fails on those and
returns zero pages, so the extractor strips any such preamble before
opening the file.

Rows are emitted newest-first to match the convention used by the other
extractors and ``normalize._flag_chain_breaks``; Metrobank prints
oldest-first, so the result is reversed before returning.
"""

from __future__ import annotations

import datetime
import io
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "metrobank"

# Metrobank prints dates as "DD MMM YYYY", e.g. "06 AUG 2024". Months
# are anchored to the calendar list so a leading numeric token followed
# by an arbitrary three-letter word in a continuation line can't
# mis-trigger as a row start.
_DATE_RE = re.compile(
    r"^(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b",
    re.IGNORECASE,
)
# Optional minus, digits with optional thousands separators, exactly two
# decimal places. Same shape as the other extractors so normalize.py can
# parse the cleaned tokens with Decimal().
_MONEY_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Transaction\s+Money\s+out", re.IGNORECASE
)
# Lines that sit inside the table band but are not transactions: the
# Balance brought forward line that opens the table on each page (and
# carries the opening balance for direction inference) and the Closing
# Balance line that closes it.
_SKIP_MARKERS = (
    "balance brought forward",
    "closing balance",
)
# Per-page chrome printed before / after / between transaction rows.
# None of these strings ever appear inside a real transaction
# description in a Metrobank statement.
_CHROME_RE = re.compile(
    r"^MBS3C_"
    r"|^Statement\s+number\s+\d"
    r"|^Business\s+Bank\s+Account\s+number"
    r"|^Sort\s+code\s+\d"
    r"|^Your\s+transactions\s*$",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Fallback direction tokens used only when the balance-differential
# check can't decide (e.g., the very first row when no opening balance
# was captured). Matched against the leading words of the description.
_DESC_OUT_PREFIXES = (
    "outward faster payment",
    "direct debit",
    "transaction charges",
    "debit interest",
    "card payment",
    "account to account transfer",
)
_DESC_IN_PREFIXES = (
    "bacs payment received",
    "inward faster payment",
    "credit",
    "deposit",
)


@dataclass
class _ParsedRow:
    """Row buffered for description continuations after the opening line."""

    date: str
    description: str
    amount: str
    balance: str


class MetrobankExtractor:
    """Extracts transactions from Metro Bank UK PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Metrobank statement.

        Args:
            pdf_path: Path to the Metrobank PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        rows, start_balance = _parse(page_texts)
        log.info(
            "metrobank: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        out = _split_directions(rows, start_balance)
        # PDF prints oldest-first; flip so consumers see the newest-first
        # convention used by the HSBC, Lloyds, Barclays, and generic
        # extractors.
        out.reverse()
        return out


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR).

    Strips any preamble before the "%PDF-" magic so a stray leading byte
    (some Metrobank statements ship with one) doesn't cause pdfplumber
    to silently parse zero pages.
    """
    raw = pdf_path.read_bytes()
    idx = raw.find(b"%PDF-")
    if idx > 0:
        raw = raw[idx:]
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _is_skip_line(line: str) -> bool:
    """True for table chrome that looks like data but isn't a transaction."""
    lowered = line.lower()
    return any(marker in lowered for marker in _SKIP_MARKERS)


def _parse(page_texts: list[str]) -> tuple[list[_ParsedRow], str | None]:
    """Walk every page, returning (rows, opening_balance).

    State that survives page boundaries: any in-flight transaction whose
    description continuations span across a page break. Date inheritance
    is *not* needed — Metrobank prints a full "DD MMM YYYY" prefix on
    every transaction-opening line.
    """
    rows: list[_ParsedRow] = []
    current: _ParsedRow | None = None
    in_table = False
    start_balance: str | None = None

    for text in page_texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue

            if _TABLE_HEADER_RE.match(line):
                in_table = True
                continue
            if not in_table:
                continue

            if _is_skip_line(line):
                if start_balance is None:
                    money = _trailing_money(line)
                    if money is not None:
                        start_balance = money
                if "closing balance" in line.lower():
                    if current is not None:
                        rows.append(current)
                        current = None
                    in_table = False
                continue

            if _CHROME_RE.search(line):
                continue

            date_match = _DATE_RE.match(line)
            after_date = (
                line[date_match.end():].strip() if date_match else line
            )
            tokens = after_date.split()
            money_count = 0
            for tok in reversed(tokens):
                if _MONEY_RE.match(tok):
                    money_count += 1
                else:
                    break

            if money_count >= 2 and date_match is not None:
                # Transaction-opener: requires both a date prefix and a
                # trailing amount/balance pair. A continuation that
                # happens to end in two number-shaped tokens (or starts
                # with a date fragment) without the other half is not
                # an opener and falls through to the continuation branch.
                if current is not None:
                    rows.append(current)
                desc_tokens = tokens[: len(tokens) - money_count]
                current = _ParsedRow(
                    date=_to_iso(date_match),
                    description=" ".join(desc_tokens),
                    amount=_clean_money(tokens[-2]),
                    balance=_clean_money(tokens[-1]),
                )
            elif current is not None:
                # Continuation: append the original line (with any date
                # fragment intact) to the buffered description.
                current.description = (
                    f"{current.description} {line}".strip()
                )
            # else: orphan continuation before any transaction has opened
            # — ignore.

    if current is not None:
        rows.append(current)

    return rows, start_balance


def _trailing_money(line: str) -> str | None:
    """Return the last money token on `line`, cleaned of thousands separators."""
    match = re.search(r"-?[\d,]+\.\d{2}\s*$", line)
    if match is None:
        return None
    return _clean_money(match.group().strip())


def _to_iso(match: re.Match[str]) -> str:
    """Build an ISO 8601 date from a matched "DD MMM YYYY" prefix."""
    day = int(match.group(1))
    month = _MONTHS[match.group(2).lower()]
    year = int(match.group(3))
    return datetime.date(year, month, day).isoformat()


def _split_directions(
    rows: list[_ParsedRow], start_balance: str | None
) -> list[RawRow]:
    """Decide each row's money_in / money_out from balance differentials.

    Walks chronologically (Metrobank prints oldest-first). When the
    printed balance moves by ``+amount`` the row is money in; by
    ``-amount`` it is money out. Falls back to a description-prefix
    heuristic when the differential is inconclusive — typically only
    the very first row, when no opening balance was captured.
    """
    out: list[RawRow] = []
    prev: Decimal | None = None
    if start_balance is not None:
        try:
            prev = Decimal(start_balance)
        except InvalidOperation:
            prev = None

    tolerance = Decimal("0.01")
    for row in rows:
        try:
            cur = Decimal(row.balance)
            amt = Decimal(row.amount)
        except InvalidOperation:
            log.warning(
                "metrobank: unparseable money in %r; defaulting to money_out",
                row,
            )
            out.append(_to_raw_row(row, "out"))
            prev = None
            continue

        direction: str | None = None
        if prev is not None:
            delta = cur - prev
            if abs(delta - amt) <= tolerance:
                direction = "in"
            elif abs(delta + amt) <= tolerance:
                direction = "out"
        if direction is None:
            direction = (
                _direction_from_description(row.description) or "out"
            )
        out.append(_to_raw_row(row, direction))
        prev = cur

    return out


def _direction_from_description(desc: str) -> str | None:
    """Look at the leading words of `desc` for a known direction marker."""
    head = desc.lower()
    for prefix in _DESC_OUT_PREFIXES:
        if head.startswith(prefix):
            return "out"
    for prefix in _DESC_IN_PREFIXES:
        if head.startswith(prefix):
            return "in"
    return None


def _to_raw_row(row: _ParsedRow, direction: str) -> RawRow:
    """Convert a buffered _ParsedRow into the canonical RawRow shape."""
    money_in = row.amount if direction == "in" else ""
    money_out = "" if direction == "in" else row.amount
    return RawRow(
        date=row.date,
        description=row.description,
        money_out=money_out,
        money_in=money_in,
        balance=row.balance,
    )


def _clean_money(token: str) -> str:
    """Strip thousands separators so normalize.py can parse with Decimal()."""
    return token.replace(",", "")
