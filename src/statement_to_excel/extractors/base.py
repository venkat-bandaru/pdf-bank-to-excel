"""Extractor protocol — the contract every bank extractor must satisfy.

Using typing.Protocol (structural subtyping) rather than an ABC keeps
extractors decoupled: they don't need to import this module, they just need
to expose a matching extract() method. The dispatcher in detect.py accepts
anything that fits the protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from statement_to_excel.models import RawRow


class Extractor(Protocol):
    """Structural interface for a bank-specific PDF extractor."""

    def extract(self, pdf_path: Path, page_texts: list[str] | None = None) -> list[RawRow]:
        """Pull raw transaction rows from a PDF.

        Args:
            pdf_path: Path to the PDF (may be text or scanned).
            page_texts: Pre-computed OCR text per page, supplied when the
                detect stage has already determined the PDF is scanned.
                None means the extractor should use pdfplumber directly.

        Returns:
            Unvalidated rows; strings only. No parsing of dates or amounts.
        """
        ...
