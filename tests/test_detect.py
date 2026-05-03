"""Tests for the bank/text-vs-scanned detector in detect.py.

Covers the character-doubling artefact that pdfplumber produces for bold
text in HSBC's regulatory footer ("HSBC UK Bank plc" extracted as
"HHSSBBCC UUKK BBaannkk ppllcc"); without tolerance for that artefact
the fingerprint check would miss every real HSBC statement.
"""

from __future__ import annotations

from pathlib import Path

from statement_to_excel.detect import _marker_in, detect


def test_marker_in_plain_match() -> None:
    assert _marker_in("Statement issued by HSBC UK Bank plc.", "HSBC UK Bank plc")


def test_marker_in_doubled_match() -> None:
    """The bold-rendered footer is detected via its character-doubled form."""
    bold = "HHSSBBCC UUKK BBaannkk ppllcc,, registered in England"
    assert _marker_in(bold, "HSBC UK Bank plc")


def test_marker_in_no_match() -> None:
    assert not _marker_in("Lloyds Bank plc statement", "HSBC UK Bank plc")


def test_detect_hsbc_august_sample(samples_dir: Path) -> None:
    """End-to-end: a real HSBC PDF whose only "HSBC UK Bank plc" occurrence
    is in the doubled-bold footer is still classified as hsbc."""
    bank, kind = detect(samples_dir / "August.pdf", min_chars_per_page=100)
    assert bank == "hsbc"
    assert kind == "text"
