"""Canonical data models shared across every pipeline stage.

Extractors produce RawRow (strings only — no parsing).
normalize.py converts RawRow → Transaction (typed values).
Statement bundles a list of Transactions with metadata about the source PDF.
Config is the typed view of config.toml, constructed once at startup.
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
    found an arithmetic discrepancy.
    """

    date: datetime.date
    description: str
    money_out: Decimal | None
    money_in: Decimal | None
    balance: Decimal | None
    confidence: Literal["ok", "low"]

    def __post_init__(self) -> None:
        if (self.money_out is None) == (self.money_in is None):
            raise ValueError(
                "Exactly one of money_out / money_in must be set; "
                f"got money_out={self.money_out!r}, money_in={self.money_in!r}"
            )
        for field_name, value in (("money_out", self.money_out), ("money_in", self.money_in)):
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(
                    f"{field_name} must be Decimal, got {type(value).__name__}"
                )


@dataclass
class RawSummary:
    """Printed statement-summary totals, as raw strings.

    Banks print an "Account Summary" block (opening balance, total paid in,
    total paid out, closing balance). Extractors that can find it return this
    so normalize.py can reconcile the extracted rows against it. Strings only,
    same contract as RawRow: parsing is normalize.py's job. Any field the
    statement omits is the empty string.
    """

    opening_balance: str
    paid_in: str
    paid_out: str
    closing_balance: str


@dataclass(frozen=True)
class Reconciliation:
    """Outcome of checking extracted rows against the printed summary totals.

    This is the accountant's sanity check: the sum of the money-in column must
    equal the statement's stated "Payments In", likewise money-out, and
    ``opening + in - out`` must land on the stated closing balance. ``ok`` is
    False when any of those disagree beyond a rounding tolerance; ``issues``
    holds one human-readable line per discrepancy for the export/Notes.
    """

    opening_balance: Decimal | None
    closing_balance: Decimal | None
    stated_paid_in: Decimal | None
    stated_paid_out: Decimal | None
    extracted_paid_in: Decimal
    extracted_paid_out: Decimal
    ok: bool
    issues: tuple[str, ...]


@dataclass
class Statement:
    """The result of processing one PDF file through the full pipeline."""

    source_pdf: Path
    bank: str
    transactions: list[Transaction]
    reconciliation: Reconciliation | None = None


@dataclass(frozen=True)
class Config:
    """Typed view of config.toml, constructed by __main__.py at startup.

    All paths are resolved to absolute form before construction so that
    stage modules can use them directly without knowing the working directory.
    """

    input_dir: Path
    output_dir: Path
    failed_dir: Path
    log_dir: Path
    detect_min_chars_per_page: int
    extractor_priority: tuple[str, ...]
