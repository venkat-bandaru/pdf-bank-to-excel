# PDF Bank Statement → Excel

Converts PDF bank statements into `.xlsx` files with standardised columns:
**Date | Description | Money Out | Money In | Balance**.

Runs entirely local — no cloud APIs, no costs.

Text-based PDFs only. Scanned (image-only) PDFs are detected and logged but
not processed; see `ARCHITECTURE.md` for the rationale.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | `python --version` |

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
.venv\Scripts\python.exe -m statement_to_excel   
```

3. Find the generated `.xlsx` files in `output/`.

If a PDF cannot be processed it is moved to `failed/` with a `.log` file explaining why. All other PDFs in the batch continue processing.

Logs are written to both the terminal and `logs/run-YYYY-MM-DD.log`.

## Run with Docker

If you'd rather not install Python locally, the project ships a `Dockerfile`
and `docker-compose.yml` that bundle the runtime.

```bash
# One-time build (or after pulling code changes)
docker compose build

# Drop PDFs into ./input/, then run
docker compose run --rm converter
```

`.xlsx` files appear in `./output/`, logs in `./logs/`, and unprocessable PDFs in `./failed/`. The four folders are bind-mounted from the project root, so anything the container writes is immediately visible on your host.

`config.toml` is mounted read-only into the container — edit it on the host and the next run picks up the change without a rebuild.

## Supported banks

Per-layout extractors live in `src/statement_to_excel/extractors/`:
HSBC UK, Barclays UK, Lloyds, Metro Bank, Monzo, NatWest, Revolut, Starling,
Virgin Money, and Zempler. Anything else falls through to a best-effort
generic extractor.

All extractors operate on text-based PDFs; scanned statements are not
supported.

## Configuration

Edit `config.toml` in the project root to adjust paths, the
scanned-detection threshold, or extractor priority. No environment variables
or CLI flags are used — one file, one source of truth.

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
