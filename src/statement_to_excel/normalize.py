"""Stage 4 — coerce raw extractor output into validated Transaction objects.

This is the only place where string -> Decimal and string -> date.date parsing
happens. It also runs the balance-chain validation described in ARCHITECTURE.md:
prev_balance + money_in - money_out == balance. Rows that break the chain get
confidence="low" so the export stage can flag them for human review.
"""

from __future__ import annotations

import datetime
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from statement_to_excel.models import RawRow, Transaction

log = logging.getLogger(__name__)

_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.01")


def normalize(rows: list[RawRow], source_pdf: Path) -> list[Transaction]:
    """Parse and validate a list of raw rows from an extractor.

    Args:
        rows: Unvalidated strings from an extractor module.
        source_pdf: Used only for log messages so failures are traceable.

    Returns:
        Validated transactions. Rows with unparseable dates or amounts are
        skipped with a WARNING log; rows that break the balance chain are
        included with confidence="low".
    """
    parsed: list[Transaction] = []
    for row in rows:
        txn = _parse_row(row, source_pdf)
        if txn is not None:
            parsed.append(txn)
    return _flag_chain_breaks(parsed)


def _parse_row(row: RawRow, source_pdf: Path) -> Transaction | None:
    try:
        date = datetime.date.fromisoformat(row.date)
        money_out = _parse_money(row.money_out)
        money_in = _parse_money(row.money_in)
        balance = _parse_money(row.balance)
    except (ValueError, InvalidOperation) as exc:
        log.warning("normalize: skipping unparseable row in %s: %r (%s)", source_pdf.name, row, exc)
        return None
    try:
        return Transaction(
            date=date,
            description=row.description,
            money_out=money_out,
            money_in=money_in,
            balance=balance,
            confidence="ok",
        )
    except (ValueError, TypeError) as exc:
        log.warning("normalize: rejecting row in %s: %r (%s)", source_pdf.name, row, exc)
        return None


def _parse_money(s: str) -> Decimal | None:
    """Parse a money string into Decimal; empty string -> None."""
    cleaned = s.strip().replace(",", "") if s else ""
    if not cleaned:
        return None
    return Decimal(cleaned)


def _flag_chain_breaks(txns: list[Transaction]) -> list[Transaction]:
    """Return a copy of txns with confidence="low" wherever the running
    balance does not match the prior balance plus the signed amount.

    The extractor (generic.py) emits rows in the same order as they appear in
    the PDF: newest first. So the row directly AFTER row i in the list holds
    the chronologically-prior balance, which is the one we diff against.
    """
    if len(txns) < 2:
        return list(txns)

    flagged = list(txns)
    for i in range(len(flagged) - 1):
        cur = flagged[i]
        prv = flagged[i + 1]
        if cur.balance is None or prv.balance is None:
            continue
        delta = cur.balance - prv.balance
        signed = (cur.money_in or _ZERO) - (cur.money_out or _ZERO)
        if abs(delta - signed) > _TOLERANCE:
            log.warning(
                "normalize: balance chain broken at row %d (%s): delta=%s signed=%s",
                i, cur.date, delta, signed,
            )
            flagged[i] = Transaction(
                date=cur.date,
                description=cur.description,
                money_out=cur.money_out,
                money_in=cur.money_in,
                balance=cur.balance,
                confidence="low",
            )
    return flagged
