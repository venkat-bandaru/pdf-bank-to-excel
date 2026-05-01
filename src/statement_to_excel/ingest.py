"""Stage 1 — discover PDFs in the input directory and decide what to process.

Keeps the rest of the pipeline decoupled from filesystem concerns: everything
downstream receives a plain list of Path objects.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def discover(input_dir: Path) -> list[Path]:
    """Return all .pdf files found in input_dir (non-recursive).

    Args:
        input_dir: Directory to scan, as configured in config.toml.

    Returns:
        Sorted list of PDF paths so processing order is deterministic.
    """
    raise NotImplementedError("see ARCHITECTURE.md")
