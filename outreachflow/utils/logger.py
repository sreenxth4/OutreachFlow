"""Logging setup for OutreachFlow.

Configures dual-output logging:
- Console: human-readable with timestamps
- File: structured logs to logs/pipeline.log
"""

import logging
import os
from datetime import datetime


def setup_logger(run_id: str) -> logging.Logger:
    """Configure and return the pipeline logger.
    
    Args:
        run_id: Correlation ID for this pipeline run.
        
    Returns:
        Configured logger instance with console and file handlers.
    """
    logger = logging.getLogger("outreachflow")
    logger.setLevel(logging.DEBUG)

    # Clear any existing handlers (prevents duplicates on re-runs)
    logger.handlers.clear()

    # Create logs directory
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # File handler — detailed structured logs
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "pipeline.log"),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        f"%(asctime)s | {run_id} | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)

    # Console handler — clean output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        f"%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
