"""Extractor for Revolut Business GBP PDF statements.

Fingerprint: page text contains "Revolut Ltd" — the regulatory entity
name printed in the per-page footer of every Revolut Business statement.

Each transaction prints across the columns
``Date (UTC) | Type | Description | Money out | Money in | Balance``.
pdfplumber renders each row as a single line whose tail is the £-prefixed
amount followed by the £-prefixed running balance (the empty Money
out/Money in column collapses away). The first three uppercase letters
after the date are a type code (CAR, MOS, FEE, MOR, MOA, ATM, EXO, EXI)
that names the transaction kind and, by convention, also pins direction:
MOR/MOA/EXI are money in; CAR/MOS/FEE/ATM/EXO are money out.

Two flavours of continuation can appear below a date row:

* a description wrap (``Badminton4mVinay`` under "...badminton club at
  highwoods •") — pure description text, no money tokens.
* a foreign-currency footer (``FX Rate GBP 1 = EUR 1.133296 €3.50``) —
  contains a € or $ token but no £ token, so it does not perturb the
  ``last £ is balance, second-to-last is amount`` rule and folds into
  the description as additional context.

Per-page chrome (the "Revolut Ltd is registered..." regulatory block,
the QR-code helper text, the page number) is filtered explicitly so it
does not get folded into whichever transaction was last in flight when
a page break occurred.

Rows are emitted newest-first to match the convention used by the
other extractors and ``normalize._flag_chain_breaks``; Revolut prints
newest-first too, so no reversal is needed.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "Revolut Ltd"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# DD MMM YYYY at the start of a line. Anchors a new transaction row.
_DATE_PREFIX_RE = re.compile(
    r"^(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})\b",
    re.IGNORECASE,
)
# A £-prefixed money token. Revolut uses a non-breaking-space-style
# thousands separator (e.g. "£195 017.33"), so the digit class admits
# spaces; the trailing "\.\d{2}" anchors the token's end so the regex
# does not over-extend across two adjacent amounts.
_MONEY_RE = re.compile(r"£\s*(-?[\d ]+\.\d{2})")
# Type code: the first 3-letter uppercase token after the date prefix.
_TYPE_CODE_RE = re.compile(r"^([A-Z]{3})\b")
# Opens the transaction table — gating signal so the balance summary
# block above (which carries its own £-amounts) cannot leak into rows.
_TABLE_HEADER_RE = re.compile(r"^Date\s*\(UTC\)\s+Description", re.IGNORECASE)
# Closes the transaction table on the final page; everything after this
# is the per-type totals summary, not a transaction.
_END_MARKER = "Transaction types"
# Per-page chrome that must not be folded into any transaction. Each
# entry is a substring test; lines matching any of these are skipped.
_FOOTER_MARKERS = (
    "Report lost or stolen card",
    "Get help directly",
    "Scan the QR code",
    "Revolut Ltd is registered",
    "and payment services under the Electronic Money",
    "and Transfer of Funds",
    "Revolut Ltd",  # © <year> Revolut Ltd
)
_PAGE_NUMBER_RE = re.compile(r"^\d+/\d+$")
_PHONE_RE = re.compile(r"^\+\d")

# Type codes whose direction is fixed by Revolut's transaction-types
# taxonomy (printed at the foot of every statement).
_TYPE_OUT = frozenset({"CAR", "MOS", "FEE", "ATM", "EXO"})
_TYPE_IN = frozenset({"MOR", "MOA", "EXI"})


@dataclass
class _Row:
    """A buffered transaction before the money_in/money_out split."""

    date: str
    type_code: str
    description: str
    amount: str
    balance: str


class RevolutBankExtractor:
    """Extracts transactions from Revolut Business GBP PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Revolut Business statement.

        Args:
            pdf_path: Path to the Revolut PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        rows = _parse(page_texts)
        log.info(
            "revolutbank: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        return [_to_raw_row(r) for r in rows]


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _parse(page_texts: list[str]) -> list[_Row]:
    """Walk every page; each date-prefixed line opens a new transaction.

    State that survives page boundaries: ``in_table`` (toggled on by the
    one-shot "Date (UTC) Description ..." header on page 1), and the
    in-flight buffer of lines that belong to whichever transaction was
    last opened — this is what lets a description wrap survive a page
    break.
    """
    rows: list[_Row] = []
    in_table = False
    buffer_date: str | None = None
    buffer_lines: list[str] = []

    def flush() -> None:
        nonlocal buffer_date, buffer_lines
        if buffer_date is not None:
            row = _row_from_buffer(buffer_date, buffer_lines)
            if row is not None:
                rows.append(row)
        buffer_date = None
        buffer_lines = []

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
            if _END_MARKER in line:
                flush()
                in_table = False
                continue
            if _is_chrome(line):
                continue

            date_match = _DATE_PREFIX_RE.match(line)
            if date_match is not None:
                flush()
                day = int(date_match.group(1))
                month = _MONTHS[date_match.group(2).lower()]
                year = int(date_match.group(3))
                buffer_date = datetime.date(year, month, day).isoformat()
                buffer_lines = [line[date_match.end():].strip()]
            elif buffer_date is not None:
                buffer_lines.append(line)

    flush()
    return rows


def _is_chrome(line: str) -> bool:
    """Return True for per-page footer / page-number lines."""
    if _PAGE_NUMBER_RE.match(line):
        return True
    if _PHONE_RE.match(line):
        return True
    return any(marker in line for marker in _FOOTER_MARKERS)


def _row_from_buffer(date: str, lines: list[str]) -> _Row | None:
    """Combine buffered lines into one transaction row.

    The last £ token is the running balance; the second-to-last is the
    transaction amount. Anything else (the type code, the description,
    any FX-rate continuation) sits to the left of the amount or to the
    right of the balance and folds into the description field.
    """
    text = " ".join(line for line in lines if line)
    if not text:
        return None

    money = list(_MONEY_RE.finditer(text))
    if len(money) < 2:
        log.warning(
            "revolutbank: skipping row at %s — fewer than two £ tokens in %r",
            date, text,
        )
        return None

    amount = _clean_money(money[-2].group(1))
    balance = _clean_money(money[-1].group(1))

    # Description spans both sides of the money pair: the type-code +
    # narrative on the left, plus any wrap or FX-rate text on the right.
    head = text[: money[-2].start()].rstrip()
    tail = text[money[-1].end():].lstrip()
    body = f"{head} {tail}".strip() if tail else head

    type_match = _TYPE_CODE_RE.match(body)
    if type_match is None:
        log.warning(
            "revolutbank: missing type code at %s in %r", date, body,
        )
        type_code = ""
        description = body
    else:
        type_code = type_match.group(1)
        description = body[type_match.end():].strip()

    return _Row(
        date=date,
        type_code=type_code,
        description=description,
        amount=amount,
        balance=balance,
    )


def _to_raw_row(row: _Row) -> RawRow:
    """Decide direction from the type code and emit the canonical RawRow."""
    if row.type_code in _TYPE_IN:
        money_in = row.amount
        money_out = ""
    elif row.type_code in _TYPE_OUT:
        money_in = ""
        money_out = row.amount
    else:
        log.warning(
            "revolutbank: unknown type code %r at %s; defaulting to money_out",
            row.type_code, row.date,
        )
        money_in = ""
        money_out = row.amount
    return RawRow(
        date=row.date,
        description=row.description,
        money_out=money_out,
        money_in=money_in,
        balance=row.balance,
    )


def _clean_money(token: str) -> str:
    """Strip the space thousands separator so normalize.py can parse with Decimal()."""
    return token.replace(" ", "")
