"""Extractor for Tide / ClearBank business PDF statements.

Fingerprint: page text contains
"Your Tide account is a bank account provided by ClearBank Limited" —
the regulatory sentence at the top of the "Bank account legal" footer
that repeats on every page.

The transaction table prints six visual columns
``Date | Transaction type | Details | Paid in (£) | Paid out (£) | Balance (£)``.
Each row spans 1–4 text lines:
  * 0–2 wrapped "Details" prefix lines (e.g.
    ``INDO-FUJI EUROPE LIMITED / ref: IFEL/INV3058/04``),
  * one anchor line
    ``<D Mon YYYY> <type> [<details tail>] [Fee (£): 0.00] <amount> <balance>``,
  * an optional ``Fee (£): 0.00`` suffix (omitted when the fee fragment
    already sat inline on the anchor),
  * an optional ``Tide Card: **** **** **** NNNN`` suffix (card
    transactions only).

Direction is *not* recoverable from the linear text alone: pdfplumber
collapses the column gap between Paid-in and Paid-out to a single
space, so the row's single amount could live in either column. We
recover direction by balance-differential against the row printed
immediately below (the chronologically-prior transaction). For the
bottom-most printed row — the chronologically-first transaction — the
fallback is the page-1 summary block, which prints both the opening
``Balance (£) on <start>`` and the closing ``Balance (£) on <end>``;
only the opening satisfies the seam, so both candidates are tried in
order.

Tide prints rows newest-first (29 Apr above 1 Feb in the sample),
matching the order ``normalize._flag_chain_breaks`` expects, so rows
are emitted in input order without reversal.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow, RawSummary

log = logging.getLogger(__name__)

FINGERPRINT = (
    "Your Tide account is a bank account provided by ClearBank Limited"
)

# Calendar months as printed in Tide's "D Mon YYYY" date tokens
# ("1 Feb 2026", "30 Apr 2026"). Drives both the anchor regex and the
# ISO conversion.
_MONTHS: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Transaction-type tokens seen in the sample. None of them is a prefix
# of another, so the alternation order is irrelevant for correctness.
_TYPES: tuple[str, ...] = (
    "Domestic Transfer",
    "Card Transaction",
    "Own Account Transfer",
    "Internal Book Transfer",
    "Fee",
)
_TYPES_RE = "|".join(re.escape(t) for t in _TYPES)

# Anchor line for a transaction row:
#   D[D] Mon YYYY <type> [details tail] [Fee (X): N.NN] amount balance
# - ``rest`` is the lazy gap between the type token and the two
#   trailing money tokens. It can be empty, a short tail like "/26"
#   wrapped from the previous Details line, a full single-line
#   description like "Savings account" or "ref: Pro plan ...", or an
#   inline "Fee (X): 0.00" fragment (stripped out below).
# - amount and balance are unsigned: Tide prints absolute values in
#   the Paid-in / Paid-out columns and a signed balance is never
#   negative in the samples we model on. A negative balance would
#   still parse because the leading "-" is not part of the money group.
_ROW_RE = re.compile(
    r"^(?P<date>\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")\s+\d{4})\s+"
    r"(?P<type>" + _TYPES_RE + r")"
    r"(?:\s+(?P<rest>.*?))?\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s+"
    r"(?P<balance>[\d,]+\.\d{2})\s*$"
)

# "Date Transaction type Details Paid in (X) Paid out (X) Balance (X)"
# — repeats at the top of every page. Parsing only starts after this
# row matches, which keeps the page-1 customer/summary block out of
# the row stream.
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Transaction\s+type\s+Details\s+Paid\s+in", re.IGNORECASE
)

# Per-row suffix lines that are structural metadata, not description.
_FEE_LINE_RE = re.compile(r"^Fee\s*\(\S\):\s*\d+\.\d{2}\s*$")
_CARD_LINE_RE = re.compile(r"^Tide Card:")

# Inline "Fee (X): 0.00" fragment that lands on the anchor line when
# the Details column for that row fit on one line; stripped before the
# tail is folded into the description.
_INLINE_FEE_RE = re.compile(r"Fee\s*\(\S\):\s*\d+\.\d{2}\s*")

# "Balance (X) on D Mon YYYY  A,MMM.NN" — printed twice in the page-1
# summary (opening + closing). Both are kept as fallback candidates
# for the chronologically-first row, because only one will satisfy
# the balance-chain seam.
_BALANCE_ON_RE = re.compile(
    r"Balance\s*\(\S\)\s*on\s+\d+\s+\S+\s+\d{4}\s+(?P<bal>[\d,]+\.\d{2})"
)

# First-line marker of the regulatory footer that repeats on each
# page. Everything from this marker to end-of-page is boilerplate.
_FOOTER_MARKER = "Bank account legal"


@dataclass
class _ParsedRow:
    """Intermediate parse result before the RawRow direction split."""

    date: str         # ISO 8601
    description: str
    amount: str       # unsigned, comma-stripped (e.g. "18000.00")
    balance: str      # signed/unsigned, comma-stripped


class TideBankExtractor:
    """Extracts transactions from Tide (ClearBank) PDF business statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a Tide statement.

        Args:
            pdf_path: Path to the Tide PDF.
            page_texts: Pre-extracted page text (the OCR path supplies
                this); if None, pdfplumber is used directly.

        Returns:
            Unvalidated raw rows, ordered newest-first. Tide already
            prints newest-first, so no reversal is applied.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        kept_lines, header_balances = _filter_chrome(page_texts)
        parsed = _parse_rows(kept_lines)
        log.info(
            "tidebank: %d row(s) parsed from %s", len(parsed), pdf_path.name
        )
        return _split_directions(parsed, header_balances)

    def summary(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> RawSummary | None:
        """Return Tide's printed summary totals, or None if not found.

        Implements the optional SummaryProvider protocol. Tide prints "Total
        paid in (£)" / "Total paid out (£)" and two "Balance (£) on <date>"
        lines — the first is the opening, the second the closing.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        return _parse_summary("\n".join(page_texts))


def _parse_summary(text: str) -> RawSummary | None:
    """Pull Tide's paid-in/paid-out totals and opening/closing balances."""
    paid_in = _find_money(r"Total paid in\s*\([£]\)\s*([\d,]+\.\d{2})", text)
    paid_out = _find_money(r"Total paid out\s*\([£]\)\s*([\d,]+\.\d{2})", text)
    balances = re.findall(
        r"Balance\s*\([£]\)\s*on\s+.*?([\d,]+\.\d{2})", text, re.IGNORECASE
    )
    opening = _summary_money(balances[0]) if balances else ""
    closing = _summary_money(balances[-1]) if len(balances) >= 2 else ""
    if not any((opening, paid_in, paid_out, closing)):
        return None
    return RawSummary(
        opening_balance=opening, paid_in=paid_in, paid_out=paid_out,
        closing_balance=closing,
    )


def _summary_money(raw: str) -> str:
    """Strip currency symbol, spaces and thousands separators (keeping sign)."""
    return re.sub(r"[£,\s]", "", raw)


def _find_money(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return _summary_money(match.group(1)) if match else ""


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _filter_chrome(
    page_texts: list[str],
) -> tuple[list[str], list[str]]:
    """Strip per-page chrome and return (table_lines, header_balances).

    Each page runs a fresh state machine:
        header → table (after the first table-header line on the page) →
        chrome (after the ``Bank account legal`` footer marker).

    While still in header mode, any ``Balance (X) on <date> <amount>``
    match is captured as a candidate previous-balance for the
    chronologically-first transaction. Page 1 prints two such lines
    (opening + closing); both are returned in order of appearance and
    the caller picks the one that satisfies the chain.
    """
    kept: list[str] = []
    candidate_balances: list[str] = []
    for text in page_texts:
        in_table = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_FOOTER_MARKER):
                break  # rest of this page is regulatory boilerplate
            if not in_table:
                if _TABLE_HEADER_RE.match(line):
                    in_table = True
                    continue
                m = _BALANCE_ON_RE.search(line)
                if m:
                    candidate_balances.append(
                        m.group("bal").replace(",", "")
                    )
                continue
            # in_table: drop the repeated header on later pages.
            if _TABLE_HEADER_RE.match(line):
                continue
            kept.append(line)
    return kept, candidate_balances


def _parse_rows(lines: list[str]) -> list[_ParsedRow]:
    """Fold non-anchor lines into the next anchor's description prefix.

    Tide prints wrapped Details content *above* the date line, so the
    prefix attaches to the next row, not the previous one. Per-row
    suffix lines (``Fee (X): N.NN`` and ``Tide Card: ****``) are
    skipped so they don't leak into the next row's prefix.
    """
    rows: list[_ParsedRow] = []
    prefix: list[str] = []
    for line in lines:
        if _FEE_LINE_RE.match(line) or _CARD_LINE_RE.match(line):
            continue
        match = _ROW_RE.match(line)
        if match is None:
            prefix.append(line)
            continue
        tail = (match.group("rest") or "").strip()
        # Strip any inline Fee fragment that landed on the anchor.
        tail = _INLINE_FEE_RE.sub("", tail).strip()
        description_parts = prefix + ([tail] if tail else [])
        rows.append(
            _ParsedRow(
                date=_to_iso(match.group("date")),
                description=" ".join(description_parts).strip(),
                amount=match.group("amount").replace(",", ""),
                balance=match.group("balance").replace(",", ""),
            )
        )
        prefix = []
    return rows


def _split_directions(
    rows: list[_ParsedRow], header_balances: list[str]
) -> list[RawRow]:
    """Decide money_in vs money_out by comparing balances.

    For every row except the last in print order, the row immediately
    below is the chronologically-prior transaction, so its balance is
    the previous balance. For the bottom-most printed row the previous
    balance comes from the page-1 summary; the opening and closing
    candidates are tried in order until one satisfies ``±amount``.
    """
    out: list[RawRow] = []
    for i, row in enumerate(rows):
        if i + 1 < len(rows):
            direction = _direction_from_delta(rows[i + 1].balance, row)
        else:
            direction = None
            for candidate in header_balances:
                direction = _direction_from_delta(candidate, row)
                if direction is not None:
                    break
        if direction is None:
            log.warning(
                "tidebank: could not determine direction for %r; "
                "defaulting to money_out",
                row,
            )
            direction = "out"
        money_in = row.amount if direction == "in" else ""
        money_out = row.amount if direction == "out" else ""
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


def _direction_from_delta(
    prev_balance: str, row: _ParsedRow
) -> str | None:
    """Return "in" / "out" / None from a row.balance vs prev_balance diff."""
    try:
        delta = Decimal(row.balance) - Decimal(prev_balance)
        amt = Decimal(row.amount)
    except InvalidOperation:
        return None
    tolerance = Decimal("0.01")
    if abs(delta - amt) <= tolerance:
        return "in"
    if abs(delta + amt) <= tolerance:
        return "out"
    return None


def _to_iso(date_str: str) -> str:
    """Convert a "D[D] Mon YYYY" token to ISO 8601."""
    day_s, mon_s, year_s = date_str.split()
    return datetime.date(int(year_s), _MONTHS[mon_s], int(day_s)).isoformat()
