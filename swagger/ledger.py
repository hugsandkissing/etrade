"""Append-only, hash-chained JSONL audit ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import jsonable


class AuditLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def writable(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return os.access(self.path.parent, os.W_OK)
        except OSError:
            return False

    def append(self, record_type: str, payload: Any) -> dict[str, Any]:
        with self.path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            previous_hash = "GENESIS"
            sequence = 1
            previous = self._tail_record(handle)
            if previous:
                previous_hash = previous["record_hash"]
                sequence = int(previous["sequence"]) + 1
            record = {
                "sequence": sequence,
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "type": record_type,
                "payload": jsonable(payload),
                "previous_hash": previous_hash,
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            record["record_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
            handle.seek(0, os.SEEK_END)
            handle.write((json.dumps(record, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle, fcntl.LOCK_UN)
        return record

    @staticmethod
    def _tail_record(handle) -> dict[str, Any] | None:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        if position == 0:
            return None
        buffer = b""
        while position > 0:
            chunk_size = min(4096, position)
            position -= chunk_size
            handle.seek(position)
            buffer = handle.read(chunk_size) + buffer
            lines = [line for line in buffer.splitlines() if line.strip()]
            if lines and (position == 0 or len(lines) >= 2):
                return json.loads(lines[-1])
        return None

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def contains_idempotency_key(self, key: str) -> bool:
        return any(
            record.get("payload", {}).get("idempotency_key") == key
            for record in self.records()
        )

    def correction(
        self, corrected_sequence: int, reason: str, replacement: dict[str, Any]
    ) -> None:
        self.append(
            "correction",
            {
                "corrected_sequence": corrected_sequence,
                "reason": reason,
                "replacement": replacement,
            },
        )

    def verify_chain(self) -> tuple[bool, str]:
        previous_hash = "GENESIS"
        expected_sequence = 1
        for record in self.records():
            record_hash = record.pop("record_hash")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            calculated = hashlib.sha256(canonical.encode()).hexdigest()
            if record_hash != calculated:
                return False, f"hash mismatch at sequence {expected_sequence}"
            if (
                record["previous_hash"] != previous_hash
                or record["sequence"] != expected_sequence
            ):
                return False, f"chain mismatch at sequence {expected_sequence}"
            previous_hash = record_hash
            expected_sequence += 1
        return True, "ok"
