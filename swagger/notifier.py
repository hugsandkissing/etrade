"""Structured logging plus optional local macOS notifications."""

from __future__ import annotations

import json
import logging
import subprocess
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


class Notifier:
    def __init__(self, logger: logging.Logger, enabled: bool = True):
        self.logger = logger
        self.enabled = enabled

    def send(self, title: str, message: str) -> None:
        emit(self.logger, "notification", title=title, message=message)
        if not self.enabled:
            return
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            emit(self.logger, "notification_failed", error=type(exc).__name__)
