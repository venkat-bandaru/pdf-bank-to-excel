"""Extractor for HSBC UK personal/business bank statements.

Fingerprint: page text contains "HSBC UK Bank plc" or "HSBC Bank plc".

HSBC's table layout is multi-line per row. Each transaction begins with a
type code (``BP``, ``DD``, ``VIS``, ``CR``, ``DR``, ``CHQ``, ``)))`` for
contactless, …) and the start of its description; zero or more continuation
lines extend the description; the final line carries the amount and, on the
last transaction of a day, the running balance. The calendar date is printed
only on the first transaction of each day and inherited by the rest,
including across page breaks.

Money direction (paid out vs paid in) is read from *which column* the amount
sits in, using word x-positions, rather than inferred from the type code.
This is what lets ambiguous codes — ``CHQ`` (a cheque can be paid in or out),
``BACS``, ``TRF`` — be classified correctly, and it means a code we have
never seen still lands in the right column. When word positions are
unavailable (the OCR/text fallback path), direction degrades to a type-code
guess.

Rows are emitted newest-first to match the convention used by
``normalize._flag_chain_breaks``.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

# HSBC's transaction type codes — the token that begins each transaction row.
# This set is used only to recognise where one transaction ends and the next
# begins; direction is read from the amount's column (see _classify_column),
# so codes that can go either way (CHQ, BACS, TRF) need no fixed direction.
# Covers HSBC's published abbreviations plus codes seen on real statements
# (BP/OBP/VIS and the ``)))`` contactless glyph are absent from HSBC's public
# list but appear throughout).
_TYPE_CODES = frozenset({
    "BP", "OBP", "DD", "VIS", "DR", "ATM", "CR", "CHQ", "SO", "BACS", "TRF",
    ")))",
})

# Fallback only: when the amount's column is unavailable (the position-less
# text path used for OCR / synthetic input), direction is guessed from the
# code, with CR treated as the sole inbound code as before.
_TYPE_IN = frozenset({"CR"})

# Money columns right-align under the "Paid out" / "Paid in" / "Balance"
# headers. A money token is assigned to the nearest column by its right edge;
# tokens further than this many points from every anchor are treated as
# description text (e.g. a reference number that merely looks like money). The
# inter-column gaps are ~80pt, so this stays well clear of the wrong column.
_COLUMN_TOLERANCE = 35.0
# Words whose top coordinates differ by no more than this belong to one line.
_LINE_TOLERANCE = 3.0

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
    direction: str = ""  # "in" | "out" | "" (unknown -> guess from type code)


@dataclass
class _Line:
    """One physical statement line with its money already column-classified.

    ``text`` keeps the descriptive words (the date and type code are still at
    the front for the assembler to strip); any money token that fell in a
    money column has been lifted out into ``amount``/``balance`` together with
    its ``direction``. ``direction`` is "" when it could not be determined
    from a column (the text fallback path).
    """

    text: str
    amount: str = ""
    direction: str = ""
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
        # The PDF path reads word positions so direction can come from the
        # column; the text path (scanned/OCR or synthetic input) cannot, and
        # falls back to a type-code guess.
        lines = _text_lines(page_texts) if page_texts is not None else _pdf_lines(pdf_path)
        txns = _assemble(lines)
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


def _pdf_lines(pdf_path: Path) -> list[_Line]:
    """Read the PDF as position-aware lines, money classified by column.

    Column anchors are taken from each page's header row and carried forward,
    so every transaction line knows where the "Paid out" / "Paid in" /
    "Balance" columns sit even on pages whose header pdfplumber renders oddly.
    """
    lines: list[_Line] = []
    with pdfplumber.open(pdf_path) as pdf:
        anchors: dict[str, float] | None = None
        for page in pdf.pages:
            grouped = _group_words(page)
            page_anchors = _column_anchors(grouped)
            if page_anchors is not None:
                anchors = page_anchors
            lines.extend(_line_from_words(words, anchors) for words in grouped)
    return lines


def _text_lines(page_texts: list[str]) -> list[_Line]:
    """Build lines from plain text (no positions); direction stays unknown."""
    return [
        _line_from_text(raw)
        for text in page_texts
        for raw in text.splitlines()
    ]


def _group_words(page: Any) -> list[list[dict[str, Any]]]:
    """Cluster a page's words into visual lines by their top coordinate."""
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    top: float | None = None
    for word in page.extract_words():
        word_top = float(word["top"])
        if top is not None and abs(word_top - top) > _LINE_TOLERANCE:
            lines.append(current)
            current = []
        current.append(word)
        top = word_top
    if current:
        lines.append(current)
    return lines


def _column_anchors(lines: list[list[dict[str, Any]]]) -> dict[str, float] | None:
    """Find the x right-edges of the Paid out / Paid in / Balance columns.

    Returns None if this page has no recognisable header row.
    """
    for words in lines:
        texts = {str(w["text"]) for w in words}
        if "Paid" not in texts or "Balance" not in texts:
            continue
        anchors: dict[str, float] = {}
        for w in words:
            label = str(w["text"])
            if label == "out":
                anchors["out"] = float(w["x1"])
            elif label == "in":
                anchors["in"] = float(w["x1"])
            elif label == "Balance":
                anchors["bal"] = float(w["x1"])
        if {"out", "in", "bal"} <= anchors.keys():
            return anchors
    return None


def _classify_column(x1: float, anchors: dict[str, float]) -> str | None:
    """Return the column ('out'/'in'/'bal') a money token's right edge sits in."""
    nearest = min(anchors, key=lambda key: abs(anchors[key] - x1))
    if abs(anchors[nearest] - x1) <= _COLUMN_TOLERANCE:
        return nearest
    return None


def _line_from_words(
    words: list[dict[str, Any]], anchors: dict[str, float] | None
) -> _Line:
    """Split a line's words into description text and column-tagged money."""
    desc: list[str] = []
    amount = ""
    direction = ""
    balance = ""
    for word in sorted(words, key=lambda w: float(w["x0"])):
        text = str(word["text"])
        column = (
            _classify_column(float(word["x1"]), anchors)
            if anchors is not None and _MONEY_RE.match(text)
            else None
        )
        if column == "bal":
            balance = _clean_money(text)
        elif column == "out":
            amount, direction = _clean_money(text), "out"
        elif column == "in":
            amount, direction = _clean_money(text), "in"
        else:
            desc.append(text)
    return _Line(text=" ".join(desc), amount=amount, direction=direction, balance=balance)


def _line_from_text(raw: str) -> _Line:
    """Fallback line builder for position-less text.

    Direction is left unknown (resolved later from the type code). Money is
    taken from the trailing tokens: one money token is the amount; two are
    "<amount> <balance>" as printed on a day-end line.
    """
    line = raw.strip()
    if not line:
        return _Line(text="")
    tokens = line.split()
    money_count = 0
    for tok in reversed(tokens):
        if _MONEY_RE.match(tok):
            money_count += 1
        else:
            break
    desc = " ".join(tokens[: len(tokens) - money_count])
    money = tokens[len(tokens) - money_count:]
    amount = ""
    balance = ""
    if money_count == 1:
        amount = _clean_money(money[0])
    elif money_count >= 2:
        amount = _clean_money(money[-2])
        balance = _clean_money(money[-1])
    return _Line(text=desc, amount=amount, balance=balance)


def _assemble(lines: list[_Line]) -> list[_Txn]:
    """Walk the lines into transactions.

    State that survives page boundaries: the calendar date most recently seen
    (HSBC omits it on rows after the first per day, including the first row
    after a page break) and any in-flight transaction whose description spans
    the break. A transaction closes as soon as its amount line arrives.
    """
    txns: list[_Txn] = []
    current: _Txn | None = None
    current_date = ""
    in_table = False

    for line in lines:
        text = line.text.strip()
        if _TABLE_HEADER_RE.match(text):
            in_table = True
            continue
        if not in_table:
            continue

        iso, text = _strip_date(text)
        if iso is not None:
            current_date = iso

        if _BAL_FORWARD_RE.search(text):
            # CARRIED FORWARD ends the table on this page; BROUGHT FORWARD
            # opens it on later pages. Neither carries a transaction.
            if "CARRIED" in text.upper():
                if current is not None:
                    txns.append(current)
                    current = None
                in_table = False
            continue

        # Stray single-character glyph (an "A" sits below the header on
        # page 1) carries no information.
        if len(text) == 1:
            text = ""

        type_code = _leading_type(text)
        if type_code is not None:
            if current is not None:
                txns.append(current)
            current = _Txn(date=current_date, type_code=type_code)
            text = text[len(type_code):].strip()

        if current is None:
            # Inside the table with no transaction open: a non-empty line with
            # no recognised type code is almost certainly a row whose leading
            # code we don't know yet — i.e. a dropped transaction. Warn loudly
            # so it is not lost silently the way OBP/ATM rows once were.
            if text:
                log.warning(
                    "hsbc: dropping unrecognised table line %r "
                    "(no known type code) — a transaction may be missing",
                    text,
                )
            continue

        if text:
            current.description = (
                f"{current.description} {text}".strip() if current.description else text
            )
        if line.amount:
            current.amount = line.amount
            if line.direction:
                current.direction = line.direction
        if line.balance:
            current.balance = line.balance
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
    return head if head in _TYPE_CODES else None


def _clean_money(token: str) -> str:
    """Strip thousands separators so normalize.py can parse with Decimal()."""
    return token.replace(",", "")


def _to_raw_row(txn: _Txn) -> RawRow:
    """Convert a buffered _Txn into the canonical RawRow shape.

    Direction comes from the column the amount sat in (set during parsing). On
    the position-less text path that is unknown, so we fall back to the old
    type-code guess: CR is inbound, everything else outbound. The type code is
    preserved at the start of the description so the export retains source
    attribution.
    """
    if txn.direction == "in":
        money_in, money_out = txn.amount, ""
    elif txn.direction == "out":
        money_in, money_out = "", txn.amount
    elif txn.type_code in _TYPE_IN:
        money_in, money_out = txn.amount, ""
    else:
        money_in, money_out = "", txn.amount
    description = f"{txn.type_code} {txn.description}".strip()
    return RawRow(
        date=txn.date,
        description=description,
        money_out=money_out,
        money_in=money_in,
        balance=txn.balance,
    )
