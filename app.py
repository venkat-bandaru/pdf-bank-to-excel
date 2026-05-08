"""Streamlit web UI for statement-to-excel.

Provides a drag-and-drop browser interface so non-technical users can
convert PDF bank statements to Excel without touching the command line.

Run with:
    streamlit run app.py

Or double-click start.bat (Windows) / start.sh (Mac/Linux).
"""

from __future__ import annotations

import logging
import tempfile
import tomllib
from pathlib import Path

import streamlit as st

from statement_to_excel import pipeline
from statement_to_excel.models import Config

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Bank Statement → Excel",
    page_icon="📄",
    layout="centered",
)

# ── Header ───────────────────────────────────────────────────────────────────

st.title("📄 Bank Statement → Excel")
st.write(
    "Upload your PDF bank statements below. "
    "Each one is converted into an Excel spreadsheet with five columns: "
    "**Date · Description · Money Out · Money In · Balance**."
)
st.write(
    "Your files are processed **entirely on this computer** — "
    "nothing is uploaded to the internet."
)

st.divider()

# ── File uploader ─────────────────────────────────────────────────────────────

uploaded_files = st.file_uploader(
    "Drop PDFs here, or click to browse",
    type="pdf",
    accept_multiple_files=True,
    help="You can select multiple files at once.",
)

convert_clicked = st.button(
    "Convert to Excel",
    type="primary",
    disabled=not uploaded_files,
)

# ── Conversion ────────────────────────────────────────────────────────────────

if convert_clicked and uploaded_files:
    # Load config from project root (same directory as this file).
    config_path = Path(__file__).parent / "config.toml"
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_dir  = tmp_path / "input"
        output_dir = tmp_path / "output"
        failed_dir = tmp_path / "failed"
        log_dir    = tmp_path / "logs"
        for d in (input_dir, output_dir, failed_dir, log_dir):
            d.mkdir()

        # Write uploaded PDFs into the temp input folder.
        for f in uploaded_files:
            (input_dir / f.name).write_bytes(f.read())

        config = Config(
            input_dir=input_dir,
            output_dir=output_dir,
            failed_dir=failed_dir,
            log_dir=log_dir,
            detect_min_chars_per_page=int(raw["detect"]["min_chars_per_page"]),
            extractor_priority=tuple(raw["extractors"]["priority"]),
        )

        # Suppress pipeline log output from cluttering the browser console.
        logging.disable(logging.CRITICAL)
        try:
            with st.spinner("Converting…"):
                pipeline.run(config)
        finally:
            logging.disable(logging.NOTSET)

        xlsx_files = sorted(output_dir.glob("*.xlsx"))
        failed_files = sorted(failed_dir.glob("*.pdf"))

        # ── Results ──────────────────────────────────────────────────────────

        if xlsx_files:
            st.success(f"✅ Converted {len(xlsx_files)} file(s). Download below:")
            for xlsx in xlsx_files:
                st.download_button(
                    label=f"⬇  {xlsx.name}",
                    data=xlsx.read_bytes(),
                    file_name=xlsx.name,
                    mime=(
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                )

        if failed_files:
            st.warning(
                f"⚠️  {len(failed_files)} file(s) could not be converted: "
                + ", ".join(f.name for f in failed_files)
            )
            st.write(
                "This usually means the PDF is a **scanned image** rather than "
                "a text-based statement. Check with your bank whether a "
                "text/digital version is available."
            )

        if not xlsx_files and not failed_files:
            st.error("No files were processed. Make sure the uploads completed.")

# ── Footer ────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Supports: HSBC · Barclays · Lloyds · Metro Bank · Monzo · NatWest · "
    "Revolut · Starling · Virgin Money · Zempler — plus a best-effort extractor "
    "for other banks."
)
