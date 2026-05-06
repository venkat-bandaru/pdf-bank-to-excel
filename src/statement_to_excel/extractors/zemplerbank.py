"""Extractor for Zempler Bank business PDF statements.

Fingerprint: page text contains "Zempler Bank Ltd" — the company name
appears in the regulatory footer that follows the transaction table
("Zempler Bank Ltd is registered in England and Wales at Cottons
Centre…").

The transaction table prints five visual columns
``Date | Card ending in | Description | Amount | Balance``. Each row
fits on one text line: a ``DD/MM/YYYY`` date, a four-digit card
suffix, a free-form description, then two signed currency tokens
(amount and running balance). The currency prefix is the literal
``£`` in some PDFs but appears as ``\\uFFFD`` (the Unicode
replacement character) when the font's CMap fails to map the £
glyph to U+00A3 — both forms are accepted.

Direction (debit vs credit) is read directly from the sign of the
Amount column: a negative Amount is ``money_out``, a positive
Amount is ``money_in``. Unlike Virgin Money — which prints unsigned
amounts in separate Debits/Credits columns and forces a balance
differential to recover direction — Zempler prints a single signed
Amount, so balance differentials are unnecessary here.

Zempler prints newest-first (25/06 above 01/06 in the sample),
matching the order ``normalize._flag_chain_breaks`` expects, so rows
are emitted in input order without reversal.
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

FINGERPRINT = "Zempler Bank Ltd"

# The currency prefix is "£" or, if the font CMap is missing, U+FFFD.
# Inside a character class to keep the row regex compact.
_POUND = r"[£�]"
# A complete transaction row:
#   DD/MM/YYYY <4-digit card> <description> <±£amount> <±£balance>
# The minus sign — when present — precedes the £ glyph in this PDF
# layout (e.g. "-£48.95"), so it is captured outside the prefix.
# The lazy ``desc`` group ends just before the first money token,
# leaving exactly the two trailing tokens to bind to amount and
# balance. Description-internal numbers in the sample (reference
# codes, account fragments like "111860******2023" and "85 LAUCHOPE
# STREET") have no £ prefix and so cannot be confused with money
# tokens.
_ROW_RE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<card>\d{4})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>-?" + _POUND + r"[\d,]+\.\d{2})\s+"
    r"(?P<balance>-?" + _POUND + r"[\d,]+\.\d{2})\s*$"
)
# The "Date Card ending in Description Amount Balance" line that
# opens the table. Header rows on page 1 (customer name and address,
# the "Opening Balance: £…" / "Closing Balance: -£…" summary, the
# "From DD/MM/YYYY to DD/MM/YYYY" period) must not be parsed as
# transactions, so collection only starts after this line.
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Card\s+ending\s+in\s+Description\s+Amount\s+Balance",
    re.IGNORECASE,
)
# Markers for the regulatory footer that follows the data table.
# Anything from here on is not transaction data.
_LEGEND_MARKERS: tuple[str, ...] = (
    "Zempler Bank Ltd is registered",
    "Zempler Bank provides credit",
)


@dataclass
class _ParsedRow:
    """Intermediate parse result before the RawRow direction split."""

    date: str
    description: str
    amount_signed: str  # signed, comma-stripped, £ stripped (e.g. "-48.95")
    balance: str        # signed, comma-stripped, £ stripped


class ZemplerBankExtractor:
    """Extracts transactions from Zempler Bank PDF business statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Zempler statement.

        Args:
            pdf_path: Path to the Zempler PDF.
            page_texts: Pre-extracted page text (the OCR path supplies
                this); if None, pdfplumber is used directly.

        Returns:
            Unvalidated raw rows, ordered newest-first. Zempler
            already prints newest-first, so no reversal is applied.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        lines = _filter_chrome(page_texts)
        parsed = _parse_rows(lines)
        log.info(
            "zemplerbank: %d row(s) parsed from %s", len(parsed), pdf_path.name
        )
        return _split_directions(parsed)


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _filter_chrome(page_texts: list[str]) -> list[str]:
    """Skip per-page chrome; keep only lines from the transaction table.

    The customer-info block on page 1 contains a
    "From 01/06/2025 to 30/06/2025" period line that would otherwise
    be picked up by the date-prefix regex. Starting collection only
    after the table header row sidesteps that. Collection stops at
    the regulatory footer.
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


def _parse_rows(lines: list[str]) -> list[_ParsedRow]:
    """Walk the kept lines, folding non-anchor lines into the previous row.

    Every transaction is a single anchor line in the sample, but the
    parser tolerates wrapped descriptions: any line that does not
    match ``_ROW_RE`` is appended to the previous row's description.
    Lines that appear before the first anchor (none expected after
    chrome filtering) are dropped.
    """
    rows: list[_ParsedRow] = []
    for line in lines:
        match = _ROW_RE.match(line)
        if match is None:
            if rows:
                rows[-1].description = f"{rows[-1].description} {line}".strip()
            continue
        rows.append(
            _ParsedRow(
                date=_to_iso(match.group("date")),
                description=match.group("desc").strip(),
                amount_signed=_strip_money(match.group("amount")),
                balance=_strip_money(match.group("balance")),
            )
        )
    return rows


def _split_directions(rows: list[_ParsedRow]) -> list[RawRow]:
    """Map each parsed row to a RawRow using the Amount sign for direction.

    Negative amount → ``money_out``, positive amount → ``money_in``.
    The stored money string drops the sign in either case (the
    canonical schema uses unsigned strings for both columns).
    """
    out: list[RawRow] = []
    for row in rows:
        if row.amount_signed.startswith("-"):
            money_out = row.amount_signed[1:]
            money_in = ""
        else:
            money_out = ""
            money_in = row.amount_signed
        out.append(
            RawRow(
                date=row.date,
                description=row.description,
                money_out=money_out,
                money_in=money_in,
                balance=row.balance,
            )
        )
    return out


def _to_iso(date_str: str) -> str:
    """Convert a "DD/MM/YYYY" date token to ISO 8601."""
    day_s, month_s, year_s = date_str.split("/")
    return datetime.date(int(year_s), int(month_s), int(day_s)).isoformat()


def _strip_money(token: str) -> str:
    """Strip the £ / U+FFFD prefix and commas; keep the leading sign."""
    if token.startswith("-"):
        return "-" + token[1:].lstrip("£�").replace(",", "")
    return token.lstrip("£�").replace(",", "")
