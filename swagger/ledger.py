"""Append-only, hash-chained JSONL audit ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import jsonable


class AuditLedger:
    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 0,
        archive_dir: Path | None = None,
    ):
        self.path = path
        self.max_bytes = max_bytes
        self.archive_dir = archive_dir or path.parent / "ledger_archive"
        self._lock = threading.Lock()
        self._idempotency_keys: set[str] = set()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def writable(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return os.access(self.path.parent, os.W_OK)
        except OSError:
            return False

    def append(self, record_type: str, payload: Any) -> dict[str, Any]:
        safe_payload = jsonable(payload)
        with self._lock:
            self._rotate_if_needed()
            with self.path.open("a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                previous_hash = "GENESIS"
                sequence = 1
                previous = self._tail_record(handle)
                if previous:
                    previous_hash = previous["record_hash"]
                    sequence = int(previous["sequence"]) + 1
                record = self._write_record(
                    handle,
                    record_type,
                    safe_payload,
                    previous_hash=previous_hash,
                    sequence=sequence,
                )
                fcntl.flock(handle, fcntl.LOCK_UN)
        if isinstance(safe_payload, dict):
            key = safe_payload.get("idempotency_key")
            if isinstance(key, str):
                self._idempotency_keys.add(key)
        return record

    @staticmethod
    def _write_record(
        handle,
        record_type: str,
        payload: Any,
        *,
        previous_hash: str,
        sequence: int,
    ) -> dict[str, Any]:
        record = {
            "sequence": sequence,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": record_type,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        record["record_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        handle.seek(0, os.SEEK_END)
        handle.write((json.dumps(record, sort_keys=True) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
        return record

    def _rotate_if_needed(self) -> Path | None:
        if (
            self.max_bytes <= 0
            or not self.path.exists()
            or self.path.stat().st_size < self.max_bytes
        ):
            return None
        with self.path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            previous = self._tail_record(handle)
            if previous is None or handle.seek(0, os.SEEK_END) < self.max_bytes:
                fcntl.flock(handle, fcntl.LOCK_UN)
                return None
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archive = self.archive_dir / (
                f"ledger-{stamp}-seq{int(previous['sequence']):012d}.jsonl"
            )
            os.replace(self.path, archive)
            fcntl.flock(handle, fcntl.LOCK_UN)

        with self.path.open("a+b") as new_handle:
            self._write_record(
                new_handle,
                "ledger_segment_started",
                {
                    "archived_file": archive.name,
                    "archived_final_hash": previous["record_hash"],
                    "archived_final_sequence": int(previous["sequence"]),
                },
                previous_hash=previous["record_hash"],
                sequence=int(previous["sequence"]) + 1,
            )
        return archive

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
        if key in self._idempotency_keys or not self.path.exists():
            return key in self._idempotency_keys
        with self.path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4 * 1024 * 1024))
            found = key.encode() in handle.read()
        if found:
            self._idempotency_keys.add(key)
        return found

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
        for index, record in enumerate(self.records()):
            if index == 0 and record.get("type") == "ledger_segment_started":
                payload = record.get("payload", {})
                previous_hash = payload.get("archived_final_hash", "")
                expected_sequence = int(payload.get("archived_final_sequence", 0)) + 1
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
