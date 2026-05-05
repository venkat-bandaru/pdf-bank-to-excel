"""Extractor for Monzo Bank business account PDF statements.

Fingerprint: page text contains "Monzo Bank Limited" — the regulatory
phrase printed in the per-page footer of every Monzo business statement.

Each transaction is one row in a six-column table
(Date | Description | Amount | Balance), but Monzo wraps long
descriptions to multiple visual lines that straddle the date row, so
the description for a single transaction can appear above, beside, and
below the date+amount+balance line. A line-by-line text parser would
mis-attribute the wrap continuations to neighbouring rows; we therefore
work from word-level positions (pdfplumber's ``extract_words``) and use
the date column as the row anchor: any word whose y-coordinate falls
into the row's band is folded into that row's description (or, for the
two rightmost money-shaped words, into amount and balance).

Direction (money in vs money out) is carried by the sign of the printed
amount: negative is money out, positive is money in. Monzo prints
transactions newest-first; that ordering is preserved so consumers
match the convention used by the other extractors and
``normalize._flag_chain_breaks``.
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

FINGERPRINT = "Monzo Bank Limited"

# DD/MM/YYYY exactly — the only date format Monzo uses inside the table.
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Money tokens: optional minus, digits with optional thousands commas,
# decimal point, exactly two digits. Same shape as the other extractors
# so normalize.py can parse the cleaned tokens with Decimal(). The
# trailing "$" deliberately rejects the "-468.00." form Monzo uses
# inside currency-conversion description text, so those tokens are not
# misread as amount/balance.
_MONEY_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
# Words used to locate the table header row on each page.
_HEADER_DATE_TEXT = "Date"
_HEADER_DESC_TEXT = "Description"
# First word of the per-page regulatory footer; a "Monzo Bank Limited"
# triple anchored to the same y closes the body band on a page.
_FOOTER_FIRST = "Monzo"
_FOOTER_SECOND = "Bank"
_FOOTER_THIRD = "Limited"
# Tolerance when grouping words into a visual row. Monzo's description
# wrap lines sit ~6.7px above/below the date row; the table header
# tokens share an exact y. 2px is well under either gap.
_Y_TOL = 2.0


@dataclass
class _Row:
    """A buffered transaction before the money_in/money_out split."""

    date: str
    description: str
    amount: str
    balance: str


class MonzoBankExtractor:
    """Extracts transactions from Monzo Bank UK PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Monzo Bank statement.

        Args:
            pdf_path: Path to the Monzo Bank PDF.
            page_texts: Ignored. Monzo statements are always text PDFs
                and the parser needs word-level positions, so we read
                the PDF directly with pdfplumber regardless.

        Returns:
            Unvalidated raw rows, ordered newest-first (matching the
            order Monzo prints them).
        """
        rows = _parse(pdf_path)
        log.info(
            "monzobank: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        return [_to_raw_row(row) for row in rows]


def _parse(pdf_path: Path) -> list[_Row]:
    """Walk every page; rows are extracted independently per page.

    Pages without a transaction table (the FSCS information page, the
    trailing blank page) yield no rows.
    """
    rows: list[_Row] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=True)
            rows.extend(_rows_from_page(words))
    return rows


def _rows_from_page(words: list[dict[str, Any]]) -> list[_Row]:
    """Convert one page's word list to transaction rows.

    Strategy: the date column anchors each row. The row's y-band runs
    from halfway-up to the previous date down to halfway-down to the
    next date (with the table header bottom and footer top capping the
    first and last rows respectively). Words inside the band — minus
    the date itself — split into the two rightmost money tokens
    (amount, balance) and everything else (description, ordered
    top-to-bottom then left-to-right).
    """
    table_top = _table_top(words)
    if table_top is None:
        return []
    footer_top = _footer_top(words, table_top)
    body = [w for w in words if table_top < w["top"] < footer_top]

    date_words = [w for w in body if _DATE_RE.match(w["text"])]
    if not date_words:
        return []
    date_words.sort(key=lambda w: w["top"])

    centers = [w["top"] for w in date_words]
    band_edges: list[float] = [table_top]
    for i in range(len(centers) - 1):
        band_edges.append((centers[i] + centers[i + 1]) / 2)
    band_edges.append(footer_top)

    rows: list[_Row] = []
    date_ids = {id(w) for w in date_words}
    for i, date_word in enumerate(date_words):
        in_band = [
            w for w in body
            if band_edges[i] <= w["top"] < band_edges[i + 1]
            and id(w) not in date_ids
        ]
        rows.append(_row_from_words(date_word, in_band))
    return rows


def _row_from_words(
    date_word: dict[str, Any], band: list[dict[str, Any]]
) -> _Row:
    """Build one _Row from the date anchor and the words in its band.

    The two rightmost money-shaped words become balance (rightmost) and
    amount; everything else, sorted by (top, x0), is the description.
    """
    money_words = sorted(
        (w for w in band if _MONEY_RE.match(w["text"])),
        key=lambda w: w["x1"],
    )
    if len(money_words) >= 2:
        amount_word = money_words[-2]
        balance_word = money_words[-1]
        consumed = {id(amount_word), id(balance_word)}
        amount = amount_word["text"]
        balance = balance_word["text"]
    elif len(money_words) == 1:
        # Anomalous row — emit what we have and let normalize flag it.
        consumed = {id(money_words[0])}
        amount = money_words[0]["text"]
        balance = ""
    else:
        consumed = set()
        amount = ""
        balance = ""

    desc_words = sorted(
        (w for w in band if id(w) not in consumed),
        key=lambda w: (round(w["top"], 1), w["x0"]),
    )
    description = " ".join(w["text"] for w in desc_words).strip()

    return _Row(
        date=_to_iso(date_word["text"]),
        description=description,
        amount=_clean_money(amount),
        balance=_clean_money(balance),
    )


def _table_top(words: list[dict[str, Any]]) -> float | None:
    """Return the bottom-y of the table header row on this page, or None.

    The header row is the one where a "Date" word and a "Description"
    word share a y. Pages without that pair (the FSCS info page, the
    trailing blank page) have no transaction table.
    """
    for w in words:
        if w["text"] != _HEADER_DATE_TEXT:
            continue
        same_row = [x for x in words if abs(x["top"] - w["top"]) < _Y_TOL]
        if any(x["text"] == _HEADER_DESC_TEXT for x in same_row):
            return float(max(x["bottom"] for x in same_row))
    return None


def _footer_top(words: list[dict[str, Any]], table_top: float) -> float:
    """Return the y at which the footer begins, or +inf if none is found.

    Scans for the first "Monzo Bank Limited" three-word sequence that
    sits below the table header. That phrase is the per-page regulatory
    footer; it never appears inside a real transaction description.
    """
    by_y: dict[float, list[dict[str, Any]]] = {}
    for w in words:
        if w["top"] <= table_top:
            continue
        by_y.setdefault(round(w["top"], 1), []).append(w)
    for y in sorted(by_y):
        row = sorted(by_y[y], key=lambda x: x["x0"])
        for i in range(len(row) - 2):
            if (
                row[i]["text"] == _FOOTER_FIRST
                and row[i + 1]["text"] == _FOOTER_SECOND
                and row[i + 2]["text"] == _FOOTER_THIRD
            ):
                # Return the matched word's actual top, not the
                # rounded bucket key — the strict-less band check
                # would otherwise admit the footer line back into the
                # last transaction's band.
                return float(row[i]["top"])
    return float("inf")


def _to_iso(text: str) -> str:
    """Convert a "DD/MM/YYYY" date token to ISO 8601."""
    match = _DATE_RE.match(text)
    if match is None:
        raise ValueError(f"not a Monzo date token: {text!r}")
    return datetime.date(
        int(match.group(3)), int(match.group(2)), int(match.group(1))
    ).isoformat()


def _to_raw_row(row: _Row) -> RawRow:
    """Decide direction from the amount sign and emit the canonical RawRow."""
    try:
        amount_dec = Decimal(row.amount)
    except InvalidOperation:
        log.warning(
            "monzobank: unparseable amount in row %r; defaulting to money_out",
            row,
        )
        return RawRow(
            date=row.date,
            description=row.description,
            money_out=row.amount,
            money_in="",
            balance=row.balance,
        )
    if amount_dec < 0:
        money_out = str(-amount_dec)
        money_in = ""
    else:
        money_out = ""
        money_in = str(amount_dec)
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
