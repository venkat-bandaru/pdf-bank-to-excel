"""Extractor for National Westminster Bank (NatWest) UK statements.

Fingerprint: page text contains "National Westminster Bank Plc" — the
regulatory phrase printed in the per-page footer ("National Westminster
Bank Plc. Registered in England & Wales No.929027.").

Each transaction is laid out across the columns
``Date | Description | Paid In(£) | Withdrawn(£) | Balance(£)``. The
amount column actually filled depends on the direction; pdfplumber's
text extraction collapses the empty column away, so a transaction's
closing line carries exactly two trailing money tokens (amount and
balance). Direction is recovered by comparing each row's balance
against the prior row's, anchored at the start by the printed
``BROUGHT FORWARD`` line — the same approach the Metrobank extractor
uses.

NatWest writes the date prefix as ``DD MMM`` (no year) on transaction
rows and as ``DD MMM YYYY`` only on the ``BROUGHT FORWARD`` line, so
the year is taken from that line and rolls forward whenever the month
goes backwards. A transaction may also omit its date entirely when it
shares a date with the immediately preceding transaction; such rows
inherit the most recently seen date.

Long descriptions wrap onto continuation lines that have no trailing
money tokens; those continuations are folded into the row that closes
on the next line containing the trailing amount + balance pair.

Rows are emitted newest-first to match the convention used by the other
extractors and ``normalize._flag_chain_breaks``; NatWest prints
oldest-first, so the result is reversed before returning.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "National Westminster Bank Plc"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Date prefix at the very start of a line. The year group is optional
# because transaction rows print ``DD MMM`` and only ``BROUGHT FORWARD``
# prints the full ``DD MMM YYYY``.
_DATE_PREFIX_RE = re.compile(
    r"^(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)
# Optional minus, digits with optional thousands separators, exactly two
# decimal places. Same shape as the other extractors so normalize.py can
# parse the cleaned tokens with Decimal().
_MONEY_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
_TABLE_HEADER_RE = re.compile(
    r"^Date\s+Description\s+Paid\s+In", re.IGNORECASE
)
# The footer that closes the table on the page that carries it. Page 2
# mentions the bank name inside FSCS prose, but never as a line that
# starts with this exact phrase + period + "Registered".
_FOOTER_RE = re.compile(
    r"^National\s+Westminster\s+Bank\s+Plc\.\s+Registered", re.IGNORECASE
)
_BROUGHT_FORWARD_RE = re.compile(r"BROUGHT\s+FORWARD", re.IGNORECASE)


@dataclass
class _ParsedRow:
    """A buffered transaction before the money_in/money_out split."""

    date: str
    description: str
    amount: str
    balance: str


class NatWestBankExtractor:
    """Extracts transactions from National Westminster Bank PDF statements."""

    def extract(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> list[RawRow]:
        """Extract raw rows from a NatWest statement.

        Args:
            pdf_path: Path to the NatWest PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line, ordered
            newest-first.
        """
        if page_texts is None:
            page_texts = _read_pdf_text(pdf_path)
        rows, start_balance = _parse(page_texts)
        log.info(
            "natwestbank: %d row(s) parsed from %s", len(rows), pdf_path.name
        )
        out = _split_directions(rows, start_balance)
        # PDF prints oldest-first; flip so consumers see the newest-first
        # convention used by the other extractors.
        out.reverse()
        return out


def _read_pdf_text(pdf_path: Path) -> list[str]:
    """Return the text of each page using pdfplumber (no OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _parse(page_texts: list[str]) -> tuple[list[_ParsedRow], str | None]:
    """Walk every page, returning (rows, opening_balance).

    State that survives page boundaries: any in-flight transaction whose
    description continuations span across a page break, and the rolling
    (year, month) used to expand the date prefixes that omit a year.
    """
    rows: list[_ParsedRow] = []
    buffer: list[str] = []
    in_table = False
    start_balance: str | None = None
    current_year: int | None = None
    current_month: int | None = None
    current_date: str | None = None

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
            if _FOOTER_RE.match(line):
                in_table = False
                buffer.clear()
                continue

            if _BROUGHT_FORWARD_RE.search(line) and start_balance is None:
                # ``DD MMM YYYY BROUGHT FORWARD <balance>``. Captures the
                # opening balance and seeds the year so subsequent
                # ``DD MMM`` prefixes can be expanded.
                match = _DATE_PREFIX_RE.match(line)
                tokens = line.split()
                if match is not None and tokens and _MONEY_RE.match(tokens[-1]):
                    start_balance = _clean_money(tokens[-1])
                    current_year = (
                        int(match.group(3)) if match.group(3) else current_year
                    )
                    current_month = _MONTHS[match.group(2).lower()]
                continue

            tokens = line.split()
            ends_in_money = (
                len(tokens) >= 2
                and _MONEY_RE.match(tokens[-1])
                and _MONEY_RE.match(tokens[-2])
            )
            if not ends_in_money:
                buffer.append(line)
                continue

            full = " ".join(buffer + [line])
            buffer.clear()
            full_tokens = full.split()
            amount = _clean_money(full_tokens[-2])
            balance = _clean_money(full_tokens[-1])
            body = " ".join(full_tokens[:-2])

            match = _DATE_PREFIX_RE.match(body)
            if match is not None:
                month = _MONTHS[match.group(2).lower()]
                year_str = match.group(3)
                if year_str is not None:
                    current_year = int(year_str)
                elif (
                    current_year is not None
                    and current_month is not None
                    and month < current_month
                ):
                    # NatWest prints chronologically; a backwards month
                    # means we've crossed a year boundary.
                    current_year += 1
                current_month = month
                if current_year is None:
                    # No BROUGHT FORWARD seen yet — leave the row's
                    # date as the raw prefix; normalize.py will reject
                    # it and the row will surface as low-confidence.
                    current_date = body[: match.end()].strip()
                else:
                    current_date = datetime.date(
                        current_year, month, int(match.group(1))
                    ).isoformat()
                description = body[match.end():].strip()
            else:
                description = body
                if current_date is None:
                    log.warning(
                        "natwestbank: row before any date prefix; "
                        "leaving date blank: %r",
                        body,
                    )

            rows.append(
                _ParsedRow(
                    date=current_date or "",
                    description=description,
                    amount=amount,
                    balance=balance,
                )
            )

    return rows, start_balance


def _split_directions(
    rows: list[_ParsedRow], start_balance: str | None
) -> list[RawRow]:
    """Decide each row's money_in / money_out from balance differentials.

    Walks chronologically (NatWest prints oldest-first). When the
    printed balance moves by ``+amount`` the row is money in; by
    ``-amount`` it is money out. Defaults to money_out if the
    differential is inconclusive — surfaces as a balance-chain break in
    normalize.py rather than as silently-wrong output.
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
                "natwestbank: unparseable money in %r; defaulting to money_out",
                row,
            )
            out.append(_to_raw_row(row, "out"))
            prev = None
            continue

        direction = "out"
        if prev is not None:
            delta = cur - prev
            if abs(delta - amt) <= tolerance:
                direction = "in"
            elif abs(delta + amt) <= tolerance:
                direction = "out"
            else:
                log.warning(
                    "natwestbank: balance differential inconclusive at %r; "
                    "defaulting to money_out",
                    row,
                )
        out.append(_to_raw_row(row, direction))
        prev = cur

    return out


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
