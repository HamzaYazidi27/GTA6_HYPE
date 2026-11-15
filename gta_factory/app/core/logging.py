"""Job-aware logging: every line goes to the console, the job log file and the dashboard feed."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..config.settings import redact

_FMT = "%(asctime)s | %(levelname)-7s | %(message)s"


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger(name: str = "gta_factory") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_RedactingFormatter(_FMT, datefmt="%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def job_logger(job_id: str, log_path: Path) -> logging.Logger:
    """Logger that also appends to ``jobs/<id>/pipeline.log`` (credentials scrubbed)."""
    logger = logging.getLogger(f"gta_factory.job.{job_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path.parent.mkdir(parents=True, exist_ok=True)
    wanted = str(log_path)
    have_file = any(isinstance(h, logging.FileHandler) and h.baseFilename == wanted for h in logger.handlers)
    if not have_file:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(_RedactingFormatter(_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(_RedactingFormatter(_FMT, datefmt="%H:%M:%S"))
        logger.addHandler(sh)
    return logger
