"""Canonical data models shared across every pipeline stage.

Extractors produce RawRow (strings only — no parsing).
normalize.py converts RawRow → Transaction (typed values).
Statement bundles a list of Transactions with metadata about the source PDF.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal


@dataclass
class RawRow:
    """Unvalidated strings as extracted directly from a PDF page.

    Extractors must not attempt to parse dates or amounts; that is
    exclusively the job of normalize.py so that parsing errors surface
    in one place.
    """

    date: str
    description: str
    money_out: str
    money_in: str
    balance: str


@dataclass(frozen=True)
class Transaction:
    """A single validated bank transaction in the canonical schema.

    Exactly one of money_out / money_in must be set (not both, not neither).
    balance may be None when the source statement omits running balances.
    confidence is "low" when the balance chain validation in normalize.py
    found an arithmetic discrepancy (likely an OCR misread).
    """

    date: datetime.date
    description: str
    money_out: Decimal | None
    money_in: Decimal | None
    balance: Decimal | None
    confidence: Literal["ok", "low"]


@dataclass
class Statement:
    """The result of processing one PDF file through the full pipeline."""

    source_pdf: Path
    bank: str
    transactions: list[Transaction]
