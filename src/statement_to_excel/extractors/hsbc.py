"""Extractor for HSBC UK personal/business bank statements.

Fingerprint: page text contains "HSBC UK Bank plc" or "HSBC Bank plc".

HSBC's table layout is multi-line per row. Each transaction begins with a
type code (``BP``, ``DD``, ``VIS``, ``CR``, ``DR``, or ``)))`` for
contactless) and the start of its description; zero or more continuation
lines extend the description; the final line ends with one money token
(amount only) or two (amount followed by the day's running balance). The
calendar date is printed only on the first transaction of each day and
inherited by the rest, including across page breaks.

Rows are emitted newest-first to match the convention used by
``normalize._flag_chain_breaks``.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow, RawSummary

log = logging.getLogger(__name__)

FINGERPRINT = "HSBC"

# A date in HSBC's "DD Mon YY" form: two-digit day, three-letter month,
# two-digit year.
_DATE_RE = re.compile(r"^(\d{2})\s+([A-Za-z]{3})\s+(\d{2})\b")
# A money token: optional minus, digits with optional thousands separators,
# always two decimal places.
_MONEY_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Payment\s+type\s+and\s+details", re.IGNORECASE
)
_BAL_FORWARD_RE = re.compile(
    r"BALANCE\s*(BROUGHT|CARRIED)\s*FORWARD", re.IGNORECASE
)

# HSBC's printed type codes. CR is the only inbound code on the statements
# we have; everything else is outbound. ``)))`` is how pdfplumber renders
# the contactless glyph. OBP is an online bill payment; ATM is a cash
# withdrawal — both outbound, and both were silently dropped before they
# were added here.
_TYPE_OUT = frozenset({"BP", "OBP", "DD", "VIS", "DR", "ATM", ")))"})
_TYPE_IN = frozenset({"CR"})

# Account Summary block on page 1. pdfplumber glues the bold labels
# ("OpeningBalance", "ClosingBalance") but keeps spaces in "Payments In" /
# "Payments Out", hence the \s* / \s+ asymmetry below.
_SUMMARY_RES: dict[str, re.Pattern[str]] = {
    "opening_balance": re.compile(r"Opening\s*Balance\s+(-?[\d,]+\.\d{2})", re.I),
    "paid_in": re.compile(r"Payments\s+In\s+(-?[\d,]+\.\d{2})", re.I),
    "paid_out": re.compile(r"Payments\s+Out\s+(-?[\d,]+\.\d{2})", re.I),
    "closing_balance": re.compile(r"Closing\s*Balance\s+(-?[\d,]+\.\d{2})", re.I),
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass
class _Txn:
    """Transaction state buffered until the closing money line arrives."""

    date: str
    type_code: str
    description: str = ""
    amount: str = ""
    balance: str = ""


class HsbcExtractor:
    """Extracts transactions from HSBC UK PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from an HSBC statement.

        Args:
            pdf_path: Path to the HSBC PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        txns = _parse(page_texts)
        log.info("hsbc: %d row(s) parsed from %s", len(txns), pdf_path.name)
        # PDF prints oldest-first; flip so consumers see the same convention
        # as the generic extractor (newest-first).
        return [_to_raw_row(t) for t in reversed(txns)]

    def summary(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> RawSummary | None:
        """Return the printed Account Summary totals, or None if not found.

        Implements the optional SummaryProvider protocol so normalize.py can
        reconcile the extracted rows against the figures HSBC prints on page 1.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        return _parse_summary("\n".join(page_texts))


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _parse_summary(text: str) -> RawSummary | None:
    """Pull the four Account Summary figures out of the full statement text.

    Returns None when none of the four labels are present (e.g. a scanned
    statement with no extractable text), so the pipeline simply skips
    reconciliation rather than reconciling against blanks.
    """
    found = {
        key: (match.group(1) if (match := pattern.search(text)) else "")
        for key, pattern in _SUMMARY_RES.items()
    }
    if not any(found.values()):
        return None
    return RawSummary(
        opening_balance=found["opening_balance"],
        paid_in=found["paid_in"],
        paid_out=found["paid_out"],
        closing_balance=found["closing_balance"],
    )


def _parse(page_texts: list[str]) -> list[_Txn]:
    """Build a list of _Txn from every page of cleaned text.

    State that survives page boundaries: the calendar date most recently
    seen (HSBC omits it on rows after the first per day, including the
    first row after a page break) and any in-flight transaction whose
    description spans across the page break.
    """
    txns: list[_Txn] = []
    current: _Txn | None = None
    current_date = ""

    for text in page_texts:
        in_table = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _TABLE_HEADER_RE.match(line):
                in_table = True
                continue
            if not in_table:
                continue

            iso, line = _strip_date(line)
            if iso is not None:
                current_date = iso
            if not line:
                continue

            if _BAL_FORWARD_RE.search(line):
                # CARRIED FORWARD ends the table on this page; BROUGHT
                # FORWARD opens it on subsequent pages. Either way the
                # line carries no transaction.
                if "CARRIED" in line.upper():
                    if current is not None:
                        txns.append(current)
                        current = None
                    in_table = False
                continue

            # Stray single-character glyphs (an "A" appears below the
            # column header on the first page) carry no information.
            if len(line) == 1:
                continue

            type_code = _leading_type(line)
            if type_code is not None:
                if current is not None:
                    txns.append(current)
                current = _Txn(date=current_date, type_code=type_code)
                line = line[len(type_code):].strip()

            if current is None:
                # Inside the table, with no transaction open, a line that
                # carries no recognised type code is almost always a row whose
                # leading code we don't know yet (e.g. a new HSBC type code) —
                # which means a transaction is being dropped. Warn loudly so it
                # is not lost silently the way OBP/ATM rows once were.
                log.warning(
                    "hsbc: dropping unrecognised table line %r "
                    "(no known type code) — a transaction may be missing",
                    line,
                )
                continue

            _consume(current, line)
            if current.amount:
                txns.append(current)
                current = None

    if current is not None:
        txns.append(current)

    return txns


def _strip_date(line: str) -> tuple[str | None, str]:
    """If `line` starts with an HSBC date, return (iso_date, remainder)."""
    match = _DATE_RE.match(line)
    if match is None:
        return None, line
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None, line
    iso = datetime.date(
        2000 + int(match.group(3)), month, int(match.group(1))
    ).isoformat()
    return iso, line[match.end():].strip()


def _leading_type(line: str) -> str | None:
    """Return the HSBC transaction type code at the start of `line`, if any."""
    head = line.split(" ", 1)[0]
    if head in _TYPE_OUT or head in _TYPE_IN:
        return head
    return None


def _consume(txn: _Txn, line: str) -> None:
    """Fold one cleaned line into `txn`, attaching trailing money if present.

    Tokens are inspected from the right: the longest run of trailing money
    tokens determines whether the line is a continuation (no money), the
    final line of a transaction (one money = amount only), or the day-end
    closing line (two money = amount followed by running balance).
    """
    if not line:
        return
    tokens = line.split()
    money_count = 0
    for tok in reversed(tokens):
        if _MONEY_RE.match(tok):
            money_count += 1
        else:
            break

    desc_tokens = tokens[: len(tokens) - money_count]
    money_tokens = tokens[len(tokens) - money_count:]

    if desc_tokens:
        addition = " ".join(desc_tokens)
        txn.description = (
            f"{txn.description} {addition}".strip() if txn.description else addition
        )

    if money_count == 1:
        txn.amount = _clean_money(money_tokens[0])
    elif money_count >= 2:
        # Day-end lines print "<amount> <balance>"; if a stray extra
        # trailing money token appears we keep the rightmost two.
        txn.amount = _clean_money(money_tokens[-2])
        txn.balance = _clean_money(money_tokens[-1])


def _clean_money(token: str) -> str:
    """Strip thousands separators so normalize.py can parse with Decimal()."""
    return token.replace(",", "")


def _to_raw_row(txn: _Txn) -> RawRow:
    """Convert a buffered _Txn into the canonical RawRow shape.

    Direction is decided by the type code: CR is inbound; everything else
    (BP, DD, VIS, DR, ``)))``) is outbound. The type code is preserved at
    the start of the description so the export retains source attribution.
    """
    direction_in = txn.type_code in _TYPE_IN
    money_in = txn.amount if direction_in else ""
    money_out = "" if direction_in else txn.amount
    description = f"{txn.type_code} {txn.description}".strip()
    return RawRow(
        date=txn.date,
        description=description,
        money_out=money_out,
        money_in=money_in,
        balance=txn.balance,
    )
