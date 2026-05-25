"""Top-level orchestrator: runs stages 1–5 for every PDF in the input directory.

On success a .xlsx lands in output/. On any stage failure the PDF is moved to
failed/ with a sibling .log, and processing continues with the next file.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from statement_to_excel import detect, export, ingest, normalize
from statement_to_excel.extractors.barclays import BarclaysExtractor
from statement_to_excel.extractors.base import Extractor
from statement_to_excel.extractors.generic import GenericExtractor
from statement_to_excel.extractors.hsbc import HsbcExtractor
from statement_to_excel.extractors.lloyds import LloydsExtractor
from statement_to_excel.extractors.metrobank import MetrobankExtractor
from statement_to_excel.extractors.monzobank import MonzoBankExtractor
from statement_to_excel.extractors.natwestbank import NatWestBankExtractor
from statement_to_excel.extractors.revolutbank import RevolutBankExtractor
from statement_to_excel.extractors.starlingbank import StarlingBankExtractor
from statement_to_excel.extractors.tidebank import TideBankExtractor
from statement_to_excel.extractors.virginmoneybank import (
    VirginMoneyBankExtractor,
)
from statement_to_excel.extractors.zemplerbank import ZemplerBankExtractor
from statement_to_excel.models import Config, Statement

log = logging.getLogger(__name__)

_EXTRACTORS: dict[str, type[Extractor]] = {
    "hsbc": HsbcExtractor,
    "barclays": BarclaysExtractor,
    "lloyds": LloydsExtractor,
    "metrobank": MetrobankExtractor,
    "monzobank": MonzoBankExtractor,
    "natwestbank": NatWestBankExtractor,
    "revolutbank": RevolutBankExtractor,
    "starlingbank": StarlingBankExtractor,
    "tidebank": TideBankExtractor,
    "virginmoneybank": VirginMoneyBankExtractor,
    "zemplerbank": ZemplerBankExtractor,
    "generic": GenericExtractor,
}


def run(config: Config) -> None:
    """Discover PDFs and run each through ingest → detect → extract → normalize → export.

    Args:
        config: Typed configuration parsed from config.toml.
    """
    pdf_paths = ingest.discover(config.input_dir)
    log.info("Found %d PDF(s) to process in %s", len(pdf_paths), config.input_dir)

    for pdf_path in pdf_paths:
        log.info("Processing %s", pdf_path.name)
        try:
            _process_one(pdf_path, config)
        except Exception:
            reason = traceback.format_exc()
            log.error("Failed to process %s:\n%s", pdf_path.name, reason)
            _move_to_failed(pdf_path, config.failed_dir, reason)


def _process_one(pdf_path: Path, config: Config) -> None:
    """Run the full stage sequence for a single PDF.

    Args:
        pdf_path: Path to the PDF being processed.
        config: Pipeline configuration.
    """
    bank_name, pdf_kind = detect.detect(pdf_path, config.detect_min_chars_per_page)
    log.info("%s: bank=%s kind=%s", pdf_path.name, bank_name, pdf_kind)

    extractor = _EXTRACTORS[bank_name]()
    # Scanned PDFs are not supported; the extractor will return an empty list
    # and the resulting .xlsx will have no transaction rows.
    raw_rows = extractor.extract(pdf_path, page_texts=None)

    transactions = normalize.normalize(raw_rows, pdf_path)
    statement = Statement(source_pdf=pdf_path, bank=bank_name, transactions=transactions)
    out_path = export.export(statement, config.output_dir)
    log.info("%s: written to %s", pdf_path.name, out_path)


def _move_to_failed(pdf_path: Path, failed_dir: Path, reason: str) -> None:
    """Move an unprocessable PDF to failed/ and write a .log sibling explaining why.

    Args:
        pdf_path: The PDF that could not be processed.
        failed_dir: Destination directory (created if absent).
        reason: Human-readable failure explanation written to the .log file.
    """
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / pdf_path.name
    pdf_path.rename(dest)
    dest.with_suffix(".log").write_text(reason, encoding="utf-8")
    log.info("Moved %s to %s", pdf_path.name, failed_dir)
