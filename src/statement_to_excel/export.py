"""Stage 5 — write a list of Transactions to a .xlsx file in the output directory.

Column order mirrors the canonical accounting schema: Date, Description,
Money Out, Money In, Balance. Rows with confidence="low" receive a "?" marker
in a Notes column so a human reviewer can spot OCR errors quickly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from statement_to_excel.models import Statement

log = logging.getLogger(__name__)


def export(statement: Statement, output_dir: Path) -> Path:
    """Serialise a Statement to a .xlsx file.

    Args:
        statement: Validated transactions plus source metadata.
        output_dir: Destination directory; created if absent.

    Returns:
        Path to the written .xlsx file.
    """
    raise NotImplementedError("see ARCHITECTURE.md")
