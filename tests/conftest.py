"""Shared pytest fixtures for the statement-to-excel test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def samples_dir() -> Path:
    """Return the path to the anonymised sample PDFs used in extractor tests."""
    return Path(__file__).parent / "samples"
