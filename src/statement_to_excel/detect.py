"""Stage 2 — identify the bank and whether a PDF is text-based or scanned.

Detection is text-fingerprint based: we look for known strings near the top of
the first page (e.g. "HSBC UK Bank plc"). The scanned/text decision uses the
character-density heuristic described in ARCHITECTURE.md.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Literal

import pdfplumber

log = logging.getLogger(__name__)

BankName = Literal[
    "hsbc", "barclays", "lloyds", "metrobank", "monzobank", "natwestbank",
    "rbsbank", "revolutbank", "starlingbank", "tidebank", "virginmoneybank",
    "zemplerbank", "generic",
]
PdfKind = Literal["text", "scanned"]

_FINGERPRINTS: tuple[tuple[BankName, tuple[str, ...]], ...] = (
    ("hsbc", ("HSBC UK Bank plc", "HSBC Bank plc")),
    ("barclays", ("Barclays Bank UK PLC", "Barclays Bank PLC")),
    ("lloyds", ("Lloyds Bank plc",)),
    ("metrobank", ("metrobank",)),
    ("monzobank", ("Monzo Bank Limited",)),
    ("natwestbank", ("National Westminster Bank Plc",)),
    ("rbsbank", ("The Royal Bank of Scotland plc.",)),
    ("revolutbank", ("Revolut Ltd",)),
    ("starlingbank", ("Starling Bank Limited",)),
    ("tidebank", (
        "Your Tide account is a bank account provided by ClearBank Limited",
    )),
    ("virginmoneybank", ("Virgin Money",)),
    ("zemplerbank", ("Zempler Bank Ltd",)),
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
    raw = pdf_path.read_bytes()
    # Some statements (notably Metrobank's) ship with a stray byte before
    # the "%PDF-" magic; pdfplumber's parser silently fails on those and
    # returns zero pages, which would route every such file to the
    # "generic" extractor with kind="scanned". Stripping the preamble
    # fixes the load without changing behaviour for normal PDFs.
    pdf_start = raw.find(b"%PDF-")
    if pdf_start > 0:
        raw = raw[pdf_start:]
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]

    total_chars = sum(len(t) for t in page_texts)
    avg = total_chars / len(page_texts) if page_texts else 0
    pdf_kind: PdfKind = "scanned" if avg < min_chars_per_page else "text"

    fingerprint_text = "\n".join(page_texts)
    bank: BankName = "generic"
    for name, markers in _FINGERPRINTS:
        if any(_marker_in(fingerprint_text, marker) for marker in markers):
            bank = name
            break

    log.debug(
        "detect: %s -> bank=%s kind=%s (avg %.1f chars/page)",
        pdf_path.name, bank, pdf_kind, avg,
    )
    return bank, pdf_kind


def _marker_in(text: str, marker: str) -> bool:
    """Return True if `marker` appears in `text`, tolerating pdfplumber's
    character-doubled rendering of bold strings.

    HSBC's regulatory footer prints "HSBC UK Bank plc" in bold, which
    pdfplumber extracts as "HHSSBBCC UUKK BBaannkk ppllcc" (every
    non-space character duplicated). Fingerprints are written in their
    natural form; this helper also checks the doubled form so detection
    survives the extraction artefact.
    """
    if marker in text:
        return True
    doubled = "".join(ch if ch.isspace() else ch * 2 for ch in marker)
    return doubled in text
