"""OCR helper used by extractors when a PDF is scanned (image-only pages).

Rasterises PDF pages via pdf2image then runs pytesseract to recover text.
Extractors call rasterise() and receive plain strings — they do not know or
care whether those strings came from pdfplumber or from here.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def rasterise(pdf_path: Path, lang: str = "eng") -> list[str]:
    """Convert each page of a scanned PDF to a string via OCR.

    Args:
        pdf_path: Path to the scanned PDF.
        lang: Tesseract language code from config.toml (default "eng").

    Returns:
        One string per page, in page order.
    """
    raise NotImplementedError("see ARCHITECTURE.md")
