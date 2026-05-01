"""Extractor for HSBC UK personal/business bank statements.

Fingerprint: page text contains "HSBC UK Bank plc" or "HSBC Bank plc".
HSBC statements use a consistent multi-column table layout across pages;
the table header row ("Date", "Payment type and details", "Paid out", "Paid in",
"Balance") is used to locate column boundaries.
"""

from __future__ import annotations

import logging
from pathlib import Path

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "HSBC"


class HsbcExtractor:
    """Extracts transactions from HSBC UK PDF statements."""

    def extract(self, pdf_path: Path, page_texts: list[str] | None = None) -> list[RawRow]:
        """Extract raw rows from an HSBC statement.

        Args:
            pdf_path: Path to the HSBC PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line.
        """
        raise NotImplementedError("see ARCHITECTURE.md")
