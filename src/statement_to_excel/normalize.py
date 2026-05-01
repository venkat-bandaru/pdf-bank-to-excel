"""Stage 4 — coerce raw extractor output into validated Transaction objects.

This is the only place where string → Decimal and string → date.date parsing
happens. It also runs the balance-chain validation described in ARCHITECTURE.md:
prev_balance + money_in - money_out == balance. Rows that break the chain get
confidence="low" so the export stage can flag them for human review.
"""

from __future__ import annotations

import logging
from pathlib import Path

from statement_to_excel.models import RawRow, Transaction

log = logging.getLogger(__name__)


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
    raise NotImplementedError("see ARCHITECTURE.md")
