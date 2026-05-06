# ── Stage 1: build ────────────────────────────────────────────────────────────
# Install Python dependencies in an isolated layer so they are cached between
# rebuilds and don't bloat the final image with build tooling.
FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and its dependencies into an explicit prefix so we can
# copy just that directory into the final stage (keeps the image smaller).
RUN pip install --no-cache-dir --prefix=/install .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# System packages:
#   tesseract-ocr  — OCR engine used by pytesseract (scanned PDF path)
#   poppler-utils  — provides pdfinfo / pdftoppm used by pdf2image
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy application source.
WORKDIR /app
COPY src/ ./src/
COPY config.toml ./

# Create the data directories. These will be shadowed by Docker volume mounts
# at runtime, but creating them here means the container works even if the
# caller forgets to mount a volume (files will land inside the container).
RUN mkdir -p input output logs failed

# Run as a non-root user — good practice, avoids accidental writes to system
# paths if something goes wrong in the pipeline.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# The pipeline is a one-shot CLI: reads input/, writes output/ + logs/ + failed/,
# then exits.  docker compose run converter  (or docker compose up) triggers it.
CMD ["python", "-m", "statement_to_excel"]
