"""Thread-safe service health state and a small localhost HTTP endpoint."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .models import HealthState


@dataclass
class HealthTracker:
    state: HealthState = HealthState.STARTING
    detail: str = "initializing"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, state: HealthState, detail: str) -> None:
        with self._lock:
            self.state = state
            self.detail = detail
            self.updated_at = datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {
                "state": self.state.value,
                "detail": self.detail,
                "updated_at": self.updated_at.isoformat().replace("+00:00", "Z"),
            }


class HealthServer:
    def __init__(self, host: str, port: int, tracker: HealthTracker):
        self.tracker = tracker
        tracker_ref = tracker

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(tracker_ref.snapshot()).encode()
                status = (
                    200
                    if tracker_ref.state in {HealthState.STARTING, HealthState.HEALTHY}
                    else 503
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
