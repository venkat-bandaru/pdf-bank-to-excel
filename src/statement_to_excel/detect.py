"""Stage 2 — identify the bank and whether a PDF is text-based or scanned.

Detection is text-fingerprint based: we look for known strings near the top of
the first page (e.g. "HSBC UK Bank plc"). The scanned/text decision uses the
character-density heuristic described in ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import pdfplumber

log = logging.getLogger(__name__)

BankName = Literal["hsbc", "barclays", "generic"]
PdfKind = Literal["text", "scanned"]

_FINGERPRINTS: tuple[tuple[BankName, tuple[str, ...]], ...] = (
    ("hsbc", ("HSBC UK Bank plc", "HSBC Bank plc")),
    ("barclays", ("Barclays Bank UK PLC", "Barclays Bank PLC")),
)


def detect(pdf_path: Path, min_chars_per_page: int) -> tuple[BankName, PdfKind]:
    """Return the bank name and whether the PDF needs OCR.

    Args:
        pdf_path: Path to the PDF to inspect.
        min_chars_per_page: Character density threshold from config.toml; below
            this the PDF is treated as scanned.

    Returns:
        A (bank_name, pdf_kind) tuple consumed by the extract stage.
    """
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]

    total_chars = sum(len(t) for t in page_texts)
    avg = total_chars / len(page_texts) if page_texts else 0
    pdf_kind: PdfKind = "scanned" if avg < min_chars_per_page else "text"

    fingerprint_text = "\n".join(page_texts)
    bank: BankName = "generic"
    for name, markers in _FINGERPRINTS:
        if any(marker in fingerprint_text for marker in markers):
            bank = name
            break

    log.debug(
        "detect: %s -> bank=%s kind=%s (avg %.1f chars/page)",
        pdf_path.name, bank, pdf_kind, avg,
    )
    return bank, pdf_kind
