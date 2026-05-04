"""Extractor for Barclays UK personal/business bank statements.

Fingerprint: page text contains "Barclays Bank UK PLC" or "Barclays Bank PLC".

Each transaction *opens* with a single line that carries (in order) an
optional "DD MMM" date prefix, the first chunk of the description, the
transaction amount, and the running balance. Any lines that follow are
description continuations until the next transaction-opening line. The
date is omitted on rows after the first per day, including the first row
after a page break.

Direction (money in vs money out) is decided by comparing each row's
balance against the previous row's balance, anchored at the start by
the printed "Start Balance" row.

Two layout traps Barclays builds in:

1. The first transaction page has a right-side "At a glance" sidebar
   whose y-baselines align with the main table's. pdfplumber's default
   text extraction interleaves the two streams. We crop the sidebar by
   x-coordinate during line reconstruction (the table content stops at
   ~x=425; the sidebar begins at ~x=439).

2. Page-break rows are printed in bold, which pdfplumber renders as
   character-doubled text ("BBaallaannccee bbrroouugghhtt ..."). The
   skip-line check tolerates both the plain and the doubled forms.

Rows are emitted newest-first to match the convention used by the other
extractors and ``normalize._flag_chain_breaks``; Barclays prints
oldest-first, so the result is reversed before returning.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "Barclays Bank UK PLC"

# A "DD MMM" date prefix, e.g. "22 Jan" or "3 Feb". Months are anchored to
# the calendar list so common description fragments like "58 On 23 Jan"
# (where "On" is *not* a month) don't mis-trigger as a row start.
_DATE_RE = re.compile(
    r"^(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
    re.IGNORECASE,
)
# Optional minus, digits with optional thousands separators, exactly two
# decimal places. Same shape as the other extractors so normalize.py can
# parse the cleaned tokens with Decimal().
_MONEY_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Description\s+Money\s+out", re.IGNORECASE
)
# "DD MMM - DD MMM YYYY" period header (e.g. "22 Jan - 21 Feb 2025") prints
# once per statement; the trailing year is the *end* year and the second
# month is the end month. Together they anchor the calendar year for
# every "DD MMM" date prefix in the table.
_PERIOD_RE = re.compile(
    r"\b\d{1,2}\s+([A-Za-z]{3})\s*[-–]\s*\d{1,2}\s+([A-Za-z]{3})\s+(\d{4})\b"
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
# Strings that identify non-transaction lines inside the table band. Each
# is matched in both its plain form and its character-doubled form (see
# _is_skip_line).
_SKIP_MARKERS = (
    "start balance",
    "balance brought forward",
    "balance carried forward",
    "total payments/receipts",
    "anything wrong",
)
# Per-page chrome printed between data rows on the page boundaries. None
# of these strings ever appear inside a real transaction description.
_CHROME_RE = re.compile(
    r"^(?:Page(?:\s+\d+)?|Continued|\d+)\s*$"
    r"|Barclays\s+Bank\s+UK\s+PLC"
    r"|Registered\s+(?:in\s+England|No\.|Office)"
    r"|Authorised\s+by\s+the\s+Prudential"
    r"|Sort\s+Code\s*\d"
    r"|^to\s+get\s+in\s+touch",
    re.IGNORECASE,
)
_TABLE_END_RE = re.compile(
    r"carried\s+forward|Total\s+Payments", re.IGNORECASE
)
# x-coordinate (in PDF user-space points) at which the right-side "At a
# glance" sidebar begins on the first transaction page. The main table's
# rightmost word ends near x=425; the sidebar's leftmost word starts at
# x≈439. A cutoff in between cleanly separates the two streams.
_SIDEBAR_X = 435.0
# Words on the same visual baseline are within ~0.5pt; main-table rows
# are spaced ~12pt apart. 1.5pt is wide enough to absorb baseline jitter
# without merging adjacent rows.
_LINE_Y_TOL = 1.5

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Fallback direction tokens used only when the balance-differential check
# can't decide (e.g., the very first row when no opening balance was
# captured). Matched against the leading words of the description.
_DESC_OUT_PREFIXES = (
    "direct debit to",
    "card payment to",
    "on-line banking bill payment",
    "commission charges",
)
_DESC_IN_PREFIXES = (
    "direct credit from",
    "deposit at",
    "unpaid direct debit",
)


@dataclass
class _ParsedRow:
    """Row buffered for description continuations after the opening line."""

    date: str
    description: str
    amount: str
    balance: str


class BarclaysExtractor:
    """Extracts transactions from Barclays UK PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Barclays statement.

        Args:
            pdf_path: Path to the Barclays PDF.
            page_texts: Ignored. Barclays' first transaction page
                interleaves a right-side sidebar with the main table at
                shared y-baselines, so the PDF is always re-opened with
                pdfplumber to get word positions; any flat OCR text
                supplied by the caller cannot be cleaned reliably.

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is not None:
            log.debug(
                "barclays: ignoring page_texts; positional extraction is required"
            )
        page_lines = _read_pdf_lines(pdf_path)
        end_month, end_year = _detect_period(page_lines)
        rows, start_balance = _parse(page_lines, end_month, end_year)
        log.info(
            "barclays: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        out = _split_directions(rows, start_balance)
        # PDF prints oldest-first; flip so consumers see the newest-first
        # convention used by the HSBC, Lloyds, and generic extractors.
        out.reverse()
        return out


def _read_pdf_lines(pdf_path: Path) -> list[list[str]]:
    """Return one list of cleaned lines per page.

    Words are filtered by x-coordinate to drop the right-side sidebar
    (only present on the first transaction page) and the right-margin
    page-number / "Page" footer marker, then grouped into lines by
    y-baseline so the parser sees a single linear stream per row.
    """
    pages: list[list[str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            table_words = [w for w in words if float(w["x0"]) < _SIDEBAR_X]
            pages.append(_words_to_lines(table_words))
    return pages


def _words_to_lines(words: list[dict[str, Any]]) -> list[str]:
    """Cluster `words` by y-baseline and return one space-joined string per row."""
    if not words:
        return []
    by_top = sorted(
        words, key=lambda w: (float(w["top"]), float(w["x0"]))
    )
    clusters: list[list[dict[str, Any]]] = [[by_top[0]]]
    cluster_top = float(by_top[0]["top"])
    for w in by_top[1:]:
        t = float(w["top"])
        if abs(t - cluster_top) <= _LINE_Y_TOL:
            clusters[-1].append(w)
        else:
            clusters.append([w])
            cluster_top = t
    out: list[str] = []
    for cluster in clusters:
        cluster.sort(key=lambda w: float(w["x0"]))
        out.append(" ".join(w["text"] for w in cluster))
    return out


def _detect_period(page_lines: list[list[str]]) -> tuple[int, int]:
    """Find the statement's end month and end year.

    Knowing both lets us assign the correct year to each "DD MMM" date
    prefix even when the statement period crosses a calendar-year
    boundary. Falls back to the first 20xx year found anywhere in the
    statement, on the assumption that the period stays within one year.
    """
    blob = "\n".join("\n".join(lines) for lines in page_lines)
    match = _PERIOD_RE.search(blob)
    if match is not None:
        end_month = _MONTHS[match.group(2).lower()]
        return end_month, int(match.group(3))
    year_match = _YEAR_RE.search(blob)
    if year_match is None:
        raise ValueError("Could not detect statement year in Barclays PDF")
    return 12, int(year_match.group(1))


def _is_skip_line(line: str) -> bool:
    """True for chrome rows that look like data but aren't transactions.

    Tolerates the character-doubled form pdfplumber produces for bold
    text — e.g. "BBaallaannccee bbrroouugghhtt ffoorrwwaarrdd" must be
    recognised as "Balance brought forward".
    """
    lowered = line.lower()
    for marker in _SKIP_MARKERS:
        if marker in lowered:
            return True
        doubled = "".join(c if c.isspace() else c * 2 for c in marker)
        if doubled in lowered:
            return True
    return False


def _parse(
    page_lines: list[list[str]], end_month: int, end_year: int
) -> tuple[list[_ParsedRow], str | None]:
    """Walk every page, returning (rows, opening_balance).

    State that survives page boundaries: the most recent calendar date
    (Barclays omits it on rows after the first per day, including the
    first row after a page break) and any in-flight transaction whose
    description continuations span across a page break.
    """
    rows: list[_ParsedRow] = []
    current: _ParsedRow | None = None
    last_date = ""
    in_table = False
    start_balance: str | None = None

    for lines in page_lines:
        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            if _TABLE_HEADER_RE.match(line):
                in_table = True
                continue
            if not in_table:
                continue

            if _is_skip_line(line):
                # A "DD MMM Start Balance ..." or "DD MMM Balance carried
                # forward ..." row still sets the active date so the next
                # transaction (which may omit its date prefix) inherits it.
                date_match = _DATE_RE.match(line)
                if date_match is not None:
                    last_date = _to_iso(
                        date_match.group(1), date_match.group(2),
                        end_month, end_year,
                    )
                if start_balance is None:
                    money = _trailing_money(line)
                    if money is not None:
                        start_balance = money
                if _TABLE_END_RE.search(line):
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

            if money_count >= 2:
                # Transaction-opening line: close any prior buffered row,
                # then start a new one with the date / first-line desc /
                # amount / balance.
                if current is not None:
                    rows.append(current)
                if date_match is not None:
                    last_date = _to_iso(
                        date_match.group(1), date_match.group(2),
                        end_month, end_year,
                    )
                desc_tokens = tokens[: len(tokens) - money_count]
                amount_tok = tokens[-2]
                balance_tok = tokens[-1]
                current = _ParsedRow(
                    date=last_date,
                    description=" ".join(desc_tokens),
                    amount=_clean_money(amount_tok),
                    balance=_clean_money(balance_tok),
                )
            elif current is not None:
                # Continuation: append to the buffered description. If a
                # date prefix was matched we discard it (continuations
                # never legitimately start with a new date — the only
                # such case in practice is a "DD MMM" fragment used
                # inside a description, e.g. "Card Payment to Google One
                # On / 12 Feb / ..."), so we keep the original line.
                current.description = (
                    f"{current.description} {line}".strip()
                )
            # else: orphan continuation before any transaction has opened —
            # ignore.

    if current is not None:
        rows.append(current)

    return rows, start_balance


def _trailing_money(line: str) -> str | None:
    """Return the last money token on `line`, cleaned of thousands separators."""
    match = re.search(r"-?[\d,]+\.\d{2}\s*$", line)
    if match is None:
        return None
    return _clean_money(match.group().strip())


def _to_iso(
    day: str, month: str, end_month: int, end_year: int
) -> str:
    """Build an ISO date from a "DD MMM" prefix using the period anchor.

    Barclays date prefixes omit the year. The statement period ends in
    `end_year`; any month later than `end_month` must therefore belong
    to the prior calendar year.
    """
    m = _MONTHS[month.lower()]
    year = end_year if m <= end_month else end_year - 1
    return datetime.date(year, m, int(day)).isoformat()


def _split_directions(
    rows: list[_ParsedRow], start_balance: str | None
) -> list[RawRow]:
    """Decide each row's money_in / money_out from balance differentials.

    Walks chronologically (Barclays prints oldest-first). When the
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
                "barclays: unparseable money in %r; defaulting to money_out",
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
