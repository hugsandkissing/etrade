"""Structured local logging; external notifications are deliberately absent."""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import jsonable


def configure_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return logging.getLogger("swagger")


def emit(logger: logging.Logger, event: str, **payload: Any) -> None:
    logger.info(json.dumps({"event": event, **jsonable(payload)}, sort_keys=True))
