"""CLI entry point: python -m statement_to_excel.

Loads config.toml, sets up logging to stdout and logs/run-YYYY-MM-DD.log,
then hands off to pipeline.run_pipeline() for every PDF found by ingest.
"""

from __future__ import annotations

import datetime
import logging
import tomllib
from pathlib import Path


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
    config_path = Path(__file__).parent.parent.parent / "config.toml"
    with config_path.open("rb") as fh:
        config = tomllib.load(fh)

    _configure_logging(Path(config["paths"]["log_dir"]))
    log = logging.getLogger(__name__)
    log.info("starting statement-to-excel")

    raise NotImplementedError("see ARCHITECTURE.md — wire up pipeline.run_pipeline()")


if __name__ == "__main__":
    main()
