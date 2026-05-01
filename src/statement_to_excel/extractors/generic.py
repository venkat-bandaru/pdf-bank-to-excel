"""Fallback extractor for unrecognised bank statement layouts.

Used when no fingerprint in detect.py matches the PDF. Applies heuristics
(e.g. look for a row that parses as a date followed by a number) to make a
best-effort extraction. Expected to fail on unusual layouts; failures are
surfaced by the balance-chain check in normalize.py rather than crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from statement_to_excel.models import RawRow

log = logging.getLogger(__name__)


class GenericExtractor:
    """Best-effort heuristic extractor for unknown bank layouts."""

    def extract(self, pdf_path: Path, page_texts: list[str] | None = None) -> list[RawRow]:
        """Attempt to extract transactions using layout-agnostic heuristics.

        Args:
            pdf_path: Path to the PDF.
            page_texts: OCR text per page if the PDF is scanned; None for
                text PDFs (pdfplumber is used directly).

        Returns:
            Unvalidated raw rows. May be incomplete or empty for unusual layouts.
        """
        raise NotImplementedError("see ARCHITECTURE.md")
