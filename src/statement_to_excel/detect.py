"""Stage 2 — identify the bank and whether a PDF is text-based or scanned.

Detection is text-fingerprint based: we look for known strings near the top of
the first page (e.g. "HSBC UK Bank plc"). The scanned/text decision uses the
character-density heuristic described in ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

BankName = Literal["hsbc", "barclays", "generic"]
PdfKind = Literal["text", "scanned"]


def detect(pdf_path: Path, min_chars_per_page: int) -> tuple[BankName, PdfKind]:
    """Return the bank name and whether the PDF needs OCR.

    Args:
        pdf_path: Path to the PDF to inspect.
        min_chars_per_page: Character density threshold from config.toml; below
            this the PDF is treated as scanned.

    Returns:
        A (bank_name, pdf_kind) tuple consumed by the extract stage.
    """
    raise NotImplementedError("see ARCHITECTURE.md")
