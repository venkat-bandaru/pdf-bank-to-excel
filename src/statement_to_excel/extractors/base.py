"""Extractor protocol — the contract every bank extractor must satisfy.

Using typing.Protocol (structural subtyping) rather than an ABC keeps
extractors decoupled: they don't need to import this module, they just need
to expose a matching extract() method. The dispatcher in detect.py accepts
anything that fits the protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from statement_to_excel.models import RawRow, RawSummary


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


@runtime_checkable
class SummaryProvider(Protocol):
    """Optional capability: extract the printed statement-summary totals.

    Kept separate from ``Extractor`` on purpose. Not every bank layout prints
    a usable summary block, and forcing all extractors to implement this would
    make the common case noisier for no benefit. The pipeline checks
    ``isinstance(extractor, SummaryProvider)`` at runtime and only reconciles
    when the capability is present. Adding it to a new extractor is therefore
    additive — it does not change the ``Extractor`` contract.
    """

    def summary(
        self, pdf_path: Path, page_texts: list[str] | None = None
    ) -> RawSummary | None:
        """Return the statement's printed summary totals, or None if absent.

        Args:
            pdf_path: Path to the PDF.
            page_texts: Pre-computed text per page; None means read with
                pdfplumber directly.

        Returns:
            A RawSummary (strings) when the summary block is found, else None.
        """
        ...
