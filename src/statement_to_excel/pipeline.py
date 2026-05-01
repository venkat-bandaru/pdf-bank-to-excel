"""Top-level orchestrator: runs stages 1–5 for every PDF in the input directory.

On success a .xlsx lands in output/. On any stage failure the PDF is moved to
failed/ with a sibling .log, and processing continues with the next file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def run_pipeline(config: dict[str, Any]) -> None:
    """Discover PDFs and run each through ingest → detect → extract → normalize → export.

    Args:
        config: Parsed contents of config.toml.
    """
    raise NotImplementedError("see ARCHITECTURE.md")
