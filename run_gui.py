"""Desktop GUI wrapper for statement-to-excel (PyInstaller entry point).

Uses only stdlib tkinter so no extra dependency is needed beyond the core
package. PyInstaller bundles this into a single executable that non-technical
users can double-click.

Build command (run once on the target OS):
    pip install pyinstaller
    pyinstaller --onefile --windowed --add-data "config.toml:." run_gui.py

The resulting executable is in dist/run_gui.exe (Windows) or
dist/run_gui (Mac/Linux).
"""

from __future__ import annotations

import logging
import sys
import tempfile
import tomllib
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from statement_to_excel import pipeline
from statement_to_excel.models import Config


def _resource_path(filename: str) -> Path:
    """Return the correct path to a bundled resource.

    When running from a PyInstaller bundle, data files live in sys._MEIPASS.
    When running from source, they live next to this script.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / filename


def _load_config() -> Config:
    """Load config.toml from the bundled or source location."""
    config_path = _resource_path("config.toml")
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    # Paths inside the bundle are temp dirs; we override them at runtime.
    return Config(
        input_dir=Path(),        # overridden per run
        output_dir=Path(),       # overridden per run
        failed_dir=Path(),       # overridden per run
        log_dir=Path(),          # overridden per run
        detect_min_chars_per_page=int(raw["detect"]["min_chars_per_page"]),
        extractor_priority=tuple(raw["extractors"]["priority"]),
    )


def main() -> None:
    """Show a file-picker, run the pipeline, ask where to save results."""
    root = tk.Tk()
    root.withdraw()   # hide the blank root window immediately

    # ── Step 1: pick PDFs ────────────────────────────────────────────────────
    pdf_paths = filedialog.askopenfilenames(
        title="Select bank statement PDFs",
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
    )
    if not pdf_paths:
        return   # user cancelled

    # ── Step 2: show a simple progress window ────────────────────────────────
    progress_win = tk.Toplevel(root)
    progress_win.title("Converting…")
    progress_win.resizable(False, False)
    tk.Label(progress_win, text="Converting your statements, please wait…", padx=20, pady=10).pack()
    bar = ttk.Progressbar(progress_win, mode="indeterminate", length=280)
    bar.pack(padx=20, pady=(0, 20))
    bar.start(10)
    progress_win.update()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir  = tmp_path / "input"
            output_dir = tmp_path / "output"
            failed_dir = tmp_path / "failed"
            log_dir    = tmp_path / "logs"
            for d in (input_dir, output_dir, failed_dir, log_dir):
                d.mkdir()

            for p in pdf_paths:
                src = Path(p)
                (input_dir / src.name).write_bytes(src.read_bytes())

            base_config = _load_config()
            config = Config(
                input_dir=input_dir,
                output_dir=output_dir,
                failed_dir=failed_dir,
                log_dir=log_dir,
                detect_min_chars_per_page=base_config.detect_min_chars_per_page,
                extractor_priority=base_config.extractor_priority,
            )

            logging.disable(logging.CRITICAL)
            try:
                pipeline.run(config)
            finally:
                logging.disable(logging.NOTSET)

            xlsx_files  = sorted(output_dir.glob("*.xlsx"))
            failed_files = sorted(failed_dir.glob("*.pdf"))

            progress_win.destroy()

            if not xlsx_files:
                msg = "No files were converted."
                if failed_files:
                    msg += (
                        f"\n\n{len(failed_files)} file(s) could not be read "
                        f"(likely scanned images):\n"
                        + "\n".join(f.name for f in failed_files)
                    )
                messagebox.showerror("Nothing converted", msg)
                return

            # ── Step 3: pick save location ───────────────────────────────────
            save_dir = filedialog.askdirectory(
                title=f"Where should the {len(xlsx_files)} Excel file(s) be saved?"
            )
            if not save_dir:
                return   # user cancelled save

            saved: list[str] = []
            for xlsx in xlsx_files:
                dest = Path(save_dir) / xlsx.name
                dest.write_bytes(xlsx.read_bytes())
                saved.append(xlsx.name)

            # ── Step 4: summary ──────────────────────────────────────────────
            summary = f"Saved {len(saved)} Excel file(s) to:\n{save_dir}"
            if failed_files:
                summary += (
                    f"\n\n⚠ {len(failed_files)} file(s) could not be converted "
                    f"(likely scanned images):\n"
                    + "\n".join(f.name for f in failed_files)
                )
            messagebox.showinfo("Done", summary)

    except Exception as exc:
        try:
            progress_win.destroy()
        except Exception:
            pass
        messagebox.showerror(
            "Unexpected error",
            f"Something went wrong:\n\n{exc}\n\nPlease send this message to your IT contact.",
        )


if __name__ == "__main__":
    main()
