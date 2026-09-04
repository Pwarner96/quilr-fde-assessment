import logging
import sys


def configure_safe_logger(name: str = "fde_assessment") -> logging.Logger:
    """Return a logger with an explicit stderr handler and no content policy."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stderr))
    return logger
