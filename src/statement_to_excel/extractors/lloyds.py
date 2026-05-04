"""Extractor for Lloyds Bank UK personal/business statements.

Fingerprint: page text contains ``Lloyds Bank plc``.

Lloyds renders each transaction column with an accessibility-tag
placeholder ("Date", "Description", "Type", "Money In (£)",
"Money Out (£)", "Balance (£)") overlaid onto the same coordinates as
the value, so pdfplumber's default text extraction interleaves the two
layers character by character ("01 Aug 24" + "Date" → "D0ate 1 Aug 24"
and so on). The fix is to use ``extract_words`` with a tight
``y_tolerance`` so the label baseline (top=Y-1.9) and value baseline
(top=Y) stay separate, then identify a value row by the presence of a
DD MMM YY date in the leftmost column and slot every other word into a
column by its x-position.

Rows are emitted newest-first to match the convention used by the other
extractors and ``normalize._flag_chain_breaks`` (Lloyds prints
oldest-first; we reverse before returning).
"""

from __future__ import annotations

import datetime
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "Lloyds Bank plc"

# Two-digit day, three-letter month, two-digit year (e.g. "01 Aug 24").
_DATE_RE = re.compile(r"^(\d{2})\s+([A-Za-z]{3})\s+(\d{2})$")
_TWO_DIGITS_RE = re.compile(r"^\d{2}$")

# Column right-edges in PDF user-space x-coordinates, derived from the
# header positions on the sample. A word is assigned to the leftmost
# column whose right edge it falls within.
_COL_BOUNDS: tuple[tuple[str, float], ...] = (
    ("date",        122.0),
    ("description", 265.0),
    ("type",        322.0),
    ("money_in",    390.0),
    ("money_out",   470.0),
    ("balance",     1.0e9),
)

# Words that are part of the accessibility-tag overlay rather than real
# row data. They appear at predictable y-offsets from every transaction
# row and would otherwise pollute the description and money columns.
# "(�)" is the encoding of "(£)" used by the sample PDF — the £
# glyph is mapped to U+FFFD in the font CMap; the literal "(£)" is also
# listed so the filter survives a future PDF that ships a clean CMap.
_LABEL_WORDS = frozenset({
    "Date", "Description", "Type",
    "Money", "In", "Out", "Balance",
    "Column", "blank.",
    "(�)", "(£)",
})

# The empty Money In cell renders as "Money In (£) blank." overlaid on
# the value baseline; pdfplumber returns it as these garbage prefixes.
_PLACEHOLDER_PREFIXES = ("Moneyb", "Ilna", "n(k")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Description text extends up to ~4.5pt above and below the value
# baseline when it spills onto extra lines; widen slightly for safety.
_DESC_Y_RADIUS = 5.0


class LloydsExtractor:
    """Extracts transactions from Lloyds Bank UK PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Lloyds statement.

        Args:
            pdf_path: Path to the Lloyds PDF.
            page_texts: Ignored. Lloyds layout requires word-level
                positional data, so the PDF is always re-opened with
                pdfplumber regardless of any flat OCR text supplied by
                the caller.

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is not None:
            log.debug(
                "lloyds: ignoring page_texts; positional extraction is required"
            )
        rows: list[RawRow] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                rows.extend(_rows_from_page(page))
        log.info("lloyds: %d row(s) parsed from %s", len(rows), pdf_path.name)
        # PDF prints oldest-first; flip so consumers see the same
        # newest-first convention as the HSBC and generic extractors.
        rows.reverse()
        return rows


def _rows_from_page(page: Any) -> list[RawRow]:
    """Pull every transaction row out of one pdfplumber page."""
    raw_words = page.extract_words(y_tolerance=0.5, x_tolerance=2)
    words = [w for w in raw_words if not _is_chrome(w["text"])]
    by_top: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for w in words:
        by_top[round(float(w["top"]), 1)].append(w)
    anchors = sorted(top for top, ws in by_top.items() if _is_value_row(ws))
    return [_build_row(top, by_top) for top in anchors]


def _is_chrome(text: str) -> bool:
    """True for label, placeholder and decorative-dot tokens."""
    if text in _LABEL_WORDS or text == ".":
        return True
    return any(text.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def _is_value_row(words: list[dict[str, Any]]) -> bool:
    """A row qualifies as a transaction iff its date column reads DD MMM YY."""
    date_words = sorted(
        (w for w in words if _column_for(w["x0"]) == "date"),
        key=lambda w: w["x0"],
    )
    if len(date_words) != 3:
        return False
    day, month, year = (w["text"] for w in date_words)
    return (
        _TWO_DIGITS_RE.fullmatch(day) is not None
        and month.lower() in _MONTHS
        and _TWO_DIGITS_RE.fullmatch(year) is not None
    )


def _column_for(x: float) -> str:
    """Map an x-coordinate to a column name."""
    for name, right_edge in _COL_BOUNDS:
        if x < right_edge:
            return name
    return _COL_BOUNDS[-1][0]  # unreachable; sentinel above is +inf-ish


def _build_row(
    anchor_top: float, by_top: dict[float, list[dict[str, Any]]]
) -> RawRow:
    """Assemble a RawRow from words within ±DESC_Y_RADIUS of the anchor."""
    cols: dict[str, list[tuple[float, float, str]]] = {
        name: [] for name, _ in _COL_BOUNDS
    }
    for top, ws in by_top.items():
        if abs(top - anchor_top) > _DESC_Y_RADIUS:
            continue
        for w in ws:
            cols[_column_for(w["x0"])].append(
                (top, float(w["x0"]), w["text"])
            )
    for items in cols.values():
        items.sort(key=lambda t: (t[0], t[1]))

    type_code = " ".join(t[2] for t in cols["type"])
    description = " ".join(t[2] for t in cols["description"])
    description = (
        f"{type_code} {description}".strip() if type_code else description
    )

    return RawRow(
        date=_to_iso(" ".join(t[2] for t in cols["date"])),
        description=description,
        money_out=_clean_money(" ".join(t[2] for t in cols["money_out"])),
        money_in=_clean_money(" ".join(t[2] for t in cols["money_in"])),
        balance=_clean_money(" ".join(t[2] for t in cols["balance"])),
    )


def _to_iso(date_str: str) -> str:
    """Convert a "DD MMM YY" string to ISO 8601."""
    match = _DATE_RE.match(date_str.strip())
    if match is None:
        return date_str
    day = int(match.group(1))
    month = _MONTHS[match.group(2).lower()]
    year = 2000 + int(match.group(3))
    return datetime.date(year, month, day).isoformat()


def _clean_money(token: str) -> str:
    """Strip thousands separators so normalize.py can parse with Decimal()."""
    return token.replace(",", "")
