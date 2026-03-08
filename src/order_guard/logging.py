"""Loguru logging configuration."""

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_dir: str = "logs", level: str = "INFO"):
    """Configure loguru with console and file sinks."""
    logger.remove()

    # Console: human-readable
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    )

    # File: JSON format, daily rotation
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path / "orderguard_{time:YYYY-MM-DD}.log"),
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
        rotation="00:00",
        retention="30 days",
        serialize=True,
    )
