# PDF Bank Statement → Excel

Converts PDF bank statements into `.xlsx` files with standardised columns:
**Date | Description | Money Out | Money In | Balance**.

Runs entirely local — no cloud OCR, no API costs.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | `python --version` |
| Tesseract OCR | 5.x | Only needed for scanned (image) PDFs |
| Poppler | latest | Required by `pdf2image` for rasterising pages |

**Installing Tesseract:**
- macOS: `brew install tesseract`
- Ubuntu/Debian: `sudo apt install tesseract-ocr`
- Windows: download the installer from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)

**Installing Poppler:**
- macOS: `brew install poppler`
- Ubuntu/Debian: `sudo apt install poppler-utils`
- Windows: download from [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases)

## Install

```bash
# Clone the repo
git clone <repo-url>
cd pdf-bank-to-excel

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install the package with dev dependencies
pip install -e ".[dev]"
```

## Usage

1. Create an `input/` folder in the project root and drop your PDF bank statements into it.
2. Run:

```bash
python -m statement_to_excel
```

3. Find the generated `.xlsx` files in `output/`.

If a PDF cannot be processed it is moved to `failed/` with a `.log` file explaining why. All other PDFs in the batch continue processing.

Logs are written to both the terminal and `logs/run-YYYY-MM-DD.log`.

## Supported banks

| Bank | Layout |
|---|---|
| HSBC UK | Text and scanned PDFs |
| Barclays UK | Text and scanned PDFs |
| Other | Best-effort generic extraction |

## Configuration

Edit `config.toml` in the project root to adjust paths, OCR settings, or extractor priority. No environment variables or CLI flags are used — one file, one source of truth.

## Development

```bash
# Run tests
pytest -q

# Type check
mypy src/

# Lint
ruff check src/ tests/
```

To add support for a new bank, create a new file under `src/statement_to_excel/extractors/` implementing the `Extractor` protocol from `extractors/base.py`. See `ARCHITECTURE.md` for the full design.
