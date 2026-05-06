"""CLI entry point: python -m statement_to_excel.

Loads config.toml, sets up logging to stdout and logs/run-YYYY-MM-DD.log,
then hands off to pipeline.run() for every PDF found by ingest.
"""

from __future__ import annotations

import datetime
import logging
import tomllib
from pathlib import Path

from statement_to_excel import pipeline
from statement_to_excel.models import Config


def _configure_logging(log_dir: Path) -> None:
    """Set up root logger to write INFO+ to stdout and a dated log file.

    A new log file is created per calendar day so past runs are preserved
    without manual rotation.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run-{datetime.date.today()}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main() -> None:
    """Load config, configure logging, and run the pipeline."""
    config_path = Path.cwd() / "config.toml"
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    config = Config(
        input_dir=Path(raw["paths"]["input_dir"]).resolve(),
        output_dir=Path(raw["paths"]["output_dir"]).resolve(),
        failed_dir=Path(raw["paths"]["failed_dir"]).resolve(),
        log_dir=Path(raw["paths"]["log_dir"]).resolve(),
        detect_min_chars_per_page=raw["detect"]["min_chars_per_page"],
        extractor_priority=tuple(raw["extractors"]["priority"]),
    )

    _configure_logging(config.log_dir)
    log = logging.getLogger(__name__)
    log.info("starting statement-to-excel")

    pipeline.run(config)


if __name__ == "__main__":
    main()
