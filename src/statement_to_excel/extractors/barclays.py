"""Extractor for Barclays UK personal/business bank statements.

Fingerprint: page text contains "Barclays Bank UK PLC" or "Barclays Bank PLC".
Barclays statements vary in layout between personal and business accounts;
this extractor handles both by detecting which header row is present.
"""

from __future__ import annotations

import logging
from pathlib import Path

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)

FINGERPRINT = "Barclays"


class BarclaysExtractor:
    """Extracts transactions from Barclays UK PDF statements."""

    def extract(self, pdf_path: Path, page_texts: list[str] | None = None) -> list[RawRow]:
        """Extract raw rows from a Barclays statement.

        Args:
            pdf_path: Path to the Barclays PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows, one per transaction line.
        """
        raise NotImplementedError("see ARCHITECTURE.md")
