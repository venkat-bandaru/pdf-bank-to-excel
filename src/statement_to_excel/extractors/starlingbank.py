"""Extractor for Starling Bank PDF business statements.

Fingerprint: page text contains "Starling Bank Limited" — the regulatory
entity name printed in the per-page footer.

The transaction table has six visual columns:
``DATE | TYPE | TRANSACTION | IN | OUT | END OF DAY ACCOUNT BALANCE``.
pdfplumber's text extraction collapses adjacent empty columns into a
single space, so two rows that look identical in line-text mode may
actually have their amount in different columns: a £43.20 token in the
IN column and a £43.20 token in the OUT column both render the same
on the line. Direction therefore can't be recovered from text alone.

The rescue is x-position. Starling renders each money column with its
amounts right-aligned, so every IN amount lands at x1 ≈ 445, every OUT
amount at x1 ≈ 501, and every running-balance amount at x1 ≈ 565.
This extractor reads pdfplumber's ``extract_words`` output, anchors the
column right-edges from the printed ``IN`` / ``OUT`` / ``ACCOUNT``
header words on page 1, and classifies each money token by which anchor
its x1 is closest to. The same approach handles the awkward case of
``FASTER PAYMENT`` rows, where the type code itself is direction-blind
(deliveries paid in via Roofoods print as FASTER PAYMENT just like
salary paid out via FASTER PAYMENT).

Two row-level subtleties:

* Most rows carry a single transaction amount; only the *last* row of
  each calendar day also carries the end-of-day running balance. Rows
  whose only money token is a balance (e.g. the ``OPENING BALANCE`` row)
  are not transactions and are skipped.

* Long descriptions wrap to a continuation line a few pixels below
  (e.g. ``inv-0462)`` under "Perfect Takeaway Packs UK Limited
  (faheems"). A continuation has no DATE-column word, sits in the
  TRANSACTION column, and is within ~15 pixels of the previous
  transaction's y; those are folded back into that row's description.
  The per-page regulatory footer is far below any transaction (y ≥ 760)
  and is therefore not folded by the same rule.

Rows are emitted newest-first to match the convention used by the other
extractors and ``normalize._flag_chain_breaks``; Starling prints
oldest-first, so the result is reversed before returning.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "Starling Bank Limited"

# DD/MM/YYYY exactly — the only date format Starling uses inside the table.
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Money tokens: optional minus, digits with optional thousands commas,
# decimal point, exactly two digits, prefixed with £.
_MONEY_RE = re.compile(r"^£-?[\d,]+\.\d{2}$")

# Right edge of the DATE column. Transaction-row date tokens land at
# x0=30; the page-header "01/10/2025 - 31/12/2025 Statement" line also
# starts at x0=30 but renders at a wider font and carries no money
# tokens, so it is naturally excluded by the "needs an IN or OUT
# token" filter applied to candidate rows.
_DATE_COL_MAX_X0 = 90.0
# x-band for description / continuation text. The TRANSACTION column
# spans x0 ≈ 191–430 on the printed statement.
_TRANSACTION_COL_MIN_X0 = 190.0
_TRANSACTION_COL_MAX_X0 = 430.0
# Tolerance when grouping words into a visual row.
_Y_TOL = 2.0
# A wrap continuation sits no more than ~15 pixels below the row it
# belongs to (rows are 12 px apart). The per-page footer is at y ≥ 760
# and so cannot be folded by accident.
_CONTINUATION_MAX_GAP = 15.0


@dataclass
class _Row:
    """A buffered transaction before it is converted to a RawRow."""

    date: str
    description: str
    money_in: str
    money_out: str
    balance: str
    last_top: float = field(default=0.0)


class StarlingBankExtractor:
    """Extracts transactions from Starling Bank PDF business statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Starling Bank statement.

        Args:
            pdf_path: Path to the Starling PDF.
            page_texts: Ignored. Direction recovery requires word-level
                positions, so we read the PDF directly with pdfplumber
                regardless.

        Returns:
            Unvalidated raw rows, ordered newest-first.
        """
        rows = _parse(pdf_path)
        log.info(
            "starlingbank: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        rows.reverse()
        return [_to_raw_row(r) for r in rows]


def _parse(pdf_path: Path) -> list[_Row]:
    """Walk every page; a single set of column anchors covers all pages."""
    with pdfplumber.open(pdf_path) as pdf:
        anchors = _column_anchors(pdf.pages[0].extract_words(use_text_flow=False))
        rows: list[_Row] = []
        active: _Row | None = None
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False)
            active = _rows_from_page(words, anchors, rows, active)
    return rows


def _column_anchors(words: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Return (in_x1, out_x1, balance_x1) right-edges of the money columns.

    The header row prints the words "IN", "OUT", and "ACCOUNT" centred
    over their columns; the right-edge of each header word is the
    right-edge of its column, and every money token below right-aligns
    to that same x.
    """
    found: dict[str, float] = {}
    for w in words:
        if w["text"] in ("IN", "OUT", "ACCOUNT") and w["text"] not in found:
            found[w["text"]] = float(w["x1"])
    missing = {"IN", "OUT", "ACCOUNT"} - found.keys()
    if missing:
        raise ValueError(
            f"starlingbank: column header(s) not found: {sorted(missing)}"
        )
    return found["IN"], found["OUT"], found["ACCOUNT"]


def _rows_from_page(
    words: list[dict[str, Any]],
    anchors: tuple[float, float, float],
    rows: list[_Row],
    active: _Row | None,
) -> _Row | None:
    """Append this page's transactions to ``rows``.

    Returns the in-flight ``active`` row (the most recently appended
    transaction) so its description can absorb continuations that
    appear at the top of the next page; Starling does not actually
    do this in the sample, but the contract is cheap to keep.
    """
    by_top: dict[float, list[dict[str, Any]]] = {}
    for w in words:
        by_top.setdefault(round(w["top"], 1), []).append(w)

    for top in sorted(by_top):
        row = sorted(by_top[top], key=lambda w: w["x0"])
        new_row = _maybe_open_row(row, top, anchors)
        if new_row is not None:
            rows.append(new_row)
            active = new_row
            continue
        if active is not None and _is_continuation(row, top, active):
            extra = " ".join(
                w["text"] for w in row
                if _TRANSACTION_COL_MIN_X0 <= w["x0"] < _TRANSACTION_COL_MAX_X0
            ).strip()
            if extra:
                active.description = f"{active.description} {extra}".strip()
                active.last_top = top
    return active


def _maybe_open_row(
    row: list[dict[str, Any]],
    top: float,
    anchors: tuple[float, float, float],
) -> _Row | None:
    """Return a new ``_Row`` if this visual row is a real transaction.

    A real transaction carries a DATE-column date token *and* at least
    one money token in the IN or OUT column. The page-header line
    "01/10/2025 - 31/12/2025 Statement" carries a date at x0=30 with no
    money token and is rejected by the second clause; the
    "OPENING BALANCE £18739.04" row carries a balance with no date and
    is rejected by the first clause.
    """
    if not row:
        return None
    first = row[0]
    if not (_DATE_RE.match(first["text"]) and first["x0"] < _DATE_COL_MAX_X0):
        return None

    in_x1, out_x1, bal_x1 = anchors
    money_in = ""
    money_out = ""
    balance = ""
    for w in row:
        if not _MONEY_RE.match(w["text"]):
            continue
        column = _classify_money(float(w["x1"]), in_x1, out_x1, bal_x1)
        cleaned = _clean_money(w["text"])
        if column == "in":
            money_in = cleaned
        elif column == "out":
            money_out = cleaned
        else:
            balance = cleaned

    if not money_in and not money_out:
        # Header / OPENING BALANCE: no transaction amount.
        return None

    description = _description_text(row)
    iso = _iso_date(first["text"])
    return _Row(
        date=iso,
        description=description,
        money_in=money_in,
        money_out=money_out,
        balance=balance,
        last_top=top,
    )


def _is_continuation(
    row: list[dict[str, Any]], top: float, active: _Row
) -> bool:
    """A row is a continuation if it sits in the TRANSACTION column,
    strictly below the active transaction by at most one row-height,
    and carries no money tokens of its own (a continuation never
    closes a column).

    The strict-below check matters at page boundaries: y-coordinates
    reset to zero on a new page, so a "24hr Customer Service" header
    at the top of page 2 has a smaller y than the last transaction on
    page 1 — without the lower bound, that header would be folded
    into the last transaction's description.
    """
    gap = top - active.last_top
    if gap <= 0 or gap > _CONTINUATION_MAX_GAP:
        return False
    if any(_MONEY_RE.match(w["text"]) for w in row):
        return False
    return any(
        _TRANSACTION_COL_MIN_X0 <= w["x0"] < _TRANSACTION_COL_MAX_X0
        for w in row
    )


def _description_text(row: list[dict[str, Any]]) -> str:
    """Build a description from the TYPE and TRANSACTION columns.

    Word order is left-to-right, which preserves the way Starling
    prints multi-word type codes (``DIRECT CREDIT``, ``FASTER PAYMENT``,
    ``CHIP & PIN``) and the merchant text that follows.
    """
    desc_words = [
        w for w in row
        if not _MONEY_RE.match(w["text"])
        and not _DATE_RE.match(w["text"])
        and w["x0"] >= 90.0
        and w["x0"] < _TRANSACTION_COL_MAX_X0
    ]
    desc_words.sort(key=lambda w: w["x0"])
    return " ".join(w["text"] for w in desc_words).strip()


def _classify_money(
    x1: float, in_x1: float, out_x1: float, bal_x1: float
) -> str:
    """Return 'in' / 'out' / 'balance' based on which anchor x1 is nearest."""
    mid_io = (in_x1 + out_x1) / 2
    mid_ob = (out_x1 + bal_x1) / 2
    if x1 < mid_io:
        return "in"
    if x1 < mid_ob:
        return "out"
    return "balance"


def _iso_date(token: str) -> str:
    """Convert a "DD/MM/YYYY" date token to ISO 8601."""
    match = _DATE_RE.match(token)
    if match is None:
        raise ValueError(f"not a Starling date token: {token!r}")
    return datetime.date(
        int(match.group(3)), int(match.group(2)), int(match.group(1))
    ).isoformat()


def _clean_money(token: str) -> str:
    """Strip the £ and any thousands separators so normalize.py can parse."""
    return token.replace("£", "").replace(",", "")


def _to_raw_row(row: _Row) -> RawRow:
    """Convert a buffered ``_Row`` into the canonical ``RawRow`` shape."""
    return RawRow(
        date=row.date,
        description=row.description,
        money_out=row.money_out,
        money_in=row.money_in,
        balance=row.balance,
    )
