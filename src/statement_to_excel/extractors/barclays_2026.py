"""Extractor for Barclays UK PDF statements in the 2026 online-banking layout.

Sibling to ``barclays.py``: the same bank now emits two visually
disjoint statement formats. The dispatcher in ``detect.py`` routes
the 2026 layout here using a more-specific fingerprint than the
legacy ``Barclays Bank UK PLC`` footer string, which both formats
share. Existing customer statements in the old "At a glance sidebar"
layout continue to be handled by ``barclays.py`` unchanged.

Fingerprint: page-1 summary line ``Last night's balance £…`` — the
phrase does not appear anywhere in the legacy layout, so detect.py
can route on it before falling through to the generic Barclays
fingerprint.

The transaction table prints five columns
``Date | Description | Money in | Money out | Balance`` but each row
spans 3–6 text lines because pdfplumber's text extraction breaks the
narrow Date column at the "/" between day-month and year. A typical
row is::

    Standing Order                   <- type label (own line)
    12/05                            <- date day/month
    GLOBAL ACCOUNTANTS -£75.00 £62.89  <- desc start + signed amount + balance
    /2026                            <- year tail (date continuation)
    ACC FEE STO                      <- description continuation

Variants observed in the sample:
  * the type label may sit on its own line above the date OR be
    inline with the date ("16/03 Bill Payment -£2,000.00 …");
  * the date line may carry the start of the description after the
    DD/MM token ("05/05 CHARGES");
  * the money line may have an empty description prefix when the
    description sits entirely above or below the money line;
  * the year-tail line may carry more description after the /YYYY
    token ("/2026 COMMISSION FOR PERIOD");
  * the row's description tail can span a page break.

Direction is read directly from the amount's sign — ``-£X.XX`` is
``money_out``, ``£X.XX`` (no minus) is ``money_in``. This avoids the
balance-differential heuristic used by the legacy layout. Balance is
captured as-is; overdraft balances print with a leading minus
(``-£594.00``) and are preserved that way.

Rows are emitted newest-first; the printed order is already
newest-first, matching the convention used by
``normalize._flag_chain_breaks``.
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

FINGERPRINT = "Last night's balance"

_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Description\s+Money\s+in\s+Money\s+out\s+Balance",
    re.IGNORECASE,
)

# A row's money line ends with two £-prefixed tokens: an optionally-
# signed amount followed by an optionally-signed balance. Anchored
# to end of line so embedded numeric noise inside descriptions (PAN
# fragments, reference codes) cannot win over the real pair.
_MONEY_LINE_RE = re.compile(
    r"(?P<amount>-?£[\d,]+\.\d{2})\s+(?P<balance>-?£[\d,]+\.\d{2})\s*$"
)

# A "DD/MM" date at the start of a line. The ``(?!/)`` lookahead
# rejects embedded full DD/MM/YYYY dates from the page-1 banner; the
# real row dates always wrap so DD/MM appears separately from /YYYY.
_DATE_DDMM_RE = re.compile(r"^(?P<day>\d{2})/(?P<month>\d{2})(?!/)")

# The year-tail line "/YYYY" — always corresponds to the most
# recently emitted row.
_YEAR_TAIL_RE = re.compile(r"^/(?P<year>\d{4})\b")

# Transaction-type labels that occupy their own line above the
# DD/MM date. Exact-match against the whole line because the same
# strings, if they ever appeared mid-description, would be ambiguous;
# in the samples we have they only ever appear as the printed
# Transaction-type column value.
_TYPE_LABELS = frozenset({
    "Standing Order",
    "Direct Debit",
    "Bill Payment",
    "Counter Credit",
    "Credit Payment",
    "Contactless Card Purchase",
    "Debit",
})

# Page-1 banner shape:
#   "Showing N transactions between DD/MM/YYYY and DD/MM/YYYY ..."
# We only need the end year as a fallback when a row's year-tail
# line is missing.
_PERIOD_RE = re.compile(
    r"Showing\s+\d+\s+transactions\s+between\s+"
    r"\d{2}/\d{2}/(?P<start_year>\d{4})\s+and\s+"
    r"\d{2}/\d{2}/(?P<end_year>\d{4})"
)

_PAGE_FOOTER_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)

# End-of-table sentinel; the regulatory boilerplate that follows
# starts with this phrase on the last page.
_END_MARKER = "Need to view older transactions"


@dataclass
class _Transaction:
    """Mutable buffer for a transaction in flight."""

    date_ddmm: str       # e.g. "12/05"
    description: str
    amount: str          # signed, £-stripped (e.g. "-75.00" or "75.00")
    balance: str         # signed, £-stripped
    year_tail: str = ""  # "/YYYY" once the trailing-year line arrives


class Barclays2026Extractor:
    """Extracts transactions from Barclays UK PDF statements (2026 layout)."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a 2026-layout Barclays statement.

        Args:
            pdf_path: Path to the Barclays PDF.
            page_texts: Pre-extracted page text (the OCR path supplies
                this); if None, pdfplumber is used directly.

        Returns:
            Unvalidated raw rows, ordered newest-first. The printed
            order is already newest-first so no reversal is applied.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        fallback_year = _detect_period_end_year(page_texts)
        table_lines = _filter_chrome(page_texts)
        txns = _parse_rows(table_lines)
        log.info(
            "barclays_2026: %d row(s) parsed from %s",
            len(txns), pdf_path.name,
        )
        return [_to_raw_row(t, fallback_year) for t in txns]


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _detect_period_end_year(page_texts: list[str]) -> int:
    """Pull the period end year out of the page-1 "Showing ..." banner.

    Used as a fallback when a row's year-tail line is absent. None of
    the rows in the sample we have are missing the year tail, but the
    parser tolerates it so a single missing tail does not lose its
    row.
    """
    blob = "\n".join(page_texts)
    match = _PERIOD_RE.search(blob)
    if match is None:
        log.warning(
            "barclays_2026: 'Showing ... transactions between' banner "
            "not found; defaulting fallback year to today"
        )
        return datetime.date.today().year
    return int(match.group("end_year"))


def _filter_chrome(page_texts: list[str]) -> list[str]:
    """Return only the lines that sit between the table header and
    the end-of-table sentinel, with per-page page-number footers
    dropped.

    ``in_table`` is set on the first page's table header and stays
    True across page boundaries — Barclays does not repeat the
    header on pages 2..N, so resetting per-page would silently drop
    every row after the first page.
    """
    kept: list[str] = []
    in_table = False
    done = False
    for text in page_texts:
        if done:
            break
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _END_MARKER in line:
                done = True
                break
            if _PAGE_FOOTER_RE.match(line):
                continue
            if not in_table:
                if _TABLE_HEADER_RE.match(line):
                    in_table = True
                continue
            kept.append(line)
    return kept


def _parse_rows(lines: list[str]) -> list[_Transaction]:
    """Walk the cleaned lines, using the money line as the row anchor.

    The state machine has only two states:
      * ``"between"`` — after a row's money line emit, before the
        next row's prelude begins. Non-money lines in this state
        attach to the just-emitted row's description.
      * ``"preluding"`` — a type label or DD/MM date for the next
        row has been seen; non-money lines now belong to the upcoming
        row's pending description.
    """
    txns: list[_Transaction] = []
    pending: list[str] = []
    current_date_ddmm = ""
    state = "between"

    for line in lines:
        money_match = _MONEY_LINE_RE.search(line)
        if money_match:
            pre = line[: money_match.start()].strip()
            date_match = _DATE_DDMM_RE.match(pre)
            if date_match:
                current_date_ddmm = date_match.group(0)
                pre = pre[date_match.end():].strip()
            if pre:
                pending.append(pre)
            txns.append(
                _Transaction(
                    date_ddmm=current_date_ddmm,
                    description=" ".join(pending).strip(),
                    amount=_strip_money(money_match.group("amount")),
                    balance=_strip_money(money_match.group("balance")),
                )
            )
            pending = []
            current_date_ddmm = ""
            state = "between"
            continue

        year_match = _YEAR_TAIL_RE.match(line)
        if year_match:
            if txns and not txns[-1].year_tail:
                txns[-1].year_tail = year_match.group(0)
            after = line[year_match.end():].strip()
            if after and txns:
                txns[-1].description = (
                    f"{txns[-1].description} {after}".strip()
                )
            continue

        date_match = _DATE_DDMM_RE.match(line)
        if date_match:
            state = "preluding"
            current_date_ddmm = date_match.group(0)
            after = line[date_match.end():].strip()
            if after:
                pending.append(after)
            continue

        if line in _TYPE_LABELS:
            state = "preluding"
            pending.append(line)
            continue

        # Plain description fragment — attach by state.
        if state == "between":
            if txns:
                txns[-1].description = (
                    f"{txns[-1].description} {line}".strip()
                )
        else:
            pending.append(line)

    return txns


def _strip_money(token: str) -> str:
    """Strip the £ glyph and commas; preserve a leading minus sign."""
    if token.startswith("-£"):
        return "-" + token[2:].replace(",", "")
    if token.startswith("£"):
        return token[1:].replace(",", "")
    return token.replace(",", "")


def _to_raw_row(txn: _Transaction, fallback_year: int) -> RawRow:
    """Convert a buffered _Transaction into the canonical RawRow.

    Direction is decided by the amount's sign: a leading minus is
    money out; otherwise money in. The sign is dropped in both
    columns — the canonical schema uses unsigned strings for the
    money_in / money_out fields.
    """
    iso = _build_iso(txn.date_ddmm, txn.year_tail, fallback_year)
    if txn.amount.startswith("-"):
        money_out = txn.amount[1:]
        money_in = ""
    else:
        money_out = ""
        money_in = txn.amount
    return RawRow(
        date=iso,
        description=txn.description,
        money_out=money_out,
        money_in=money_in,
        balance=txn.balance,
    )


def _build_iso(
    date_ddmm: str, year_tail: str, fallback_year: int
) -> str:
    """Combine "DD/MM" with "/YYYY" into ISO 8601.

    Falls back to ``fallback_year`` (from the "Showing ..." banner)
    when the year-tail line is missing. Returns "" if the day/month
    cannot be parsed — normalize.py will drop the row with a warning
    rather than crashing the run.
    """
    m = _DATE_DDMM_RE.match(date_ddmm)
    if m is None:
        log.warning("barclays_2026: malformed date %r", date_ddmm)
        return ""
    day = int(m.group("day"))
    month = int(m.group("month"))
    year_m = re.match(r"^/(\d{4})$", year_tail)
    year = int(year_m.group(1)) if year_m else fallback_year
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        log.warning(
            "barclays_2026: invalid date day=%d month=%d year=%d",
            day, month, year,
        )
        return ""
