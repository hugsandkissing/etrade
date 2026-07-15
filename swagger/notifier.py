"""Structured local logging; external notifications are deliberately absent."""

from __future__ import annotations

import json
import logging
from typing import Any

from .models import jsonable


def configure_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # MCP/httpx debug logs can include ephemeral session identifiers. Keep the
    # engine's own structured events at INFO while suppressing transport noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    return logging.getLogger("swagger")


def emit(logger: logging.Logger, event: str, **payload: Any) -> None:
    logger.info(json.dumps({"event": event, **jsonable(payload)}, sort_keys=True))
