"""Evaluate evidence gates for the first guarded live launch."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config import Settings
from .report import ledger_paths


EASTERN = ZoneInfo("America/New_York")
MIN_EVALUATIONS_PER_SESSION = 100
REQUIRED_CLEAN_SESSIONS = 10
REQUIRED_CLEAR_PREVIEWS = 3


def _timestamp(record: dict[str, Any]) -> datetime:
    value = record.get("payload", {}).get("timestamp") or record["timestamp"]
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def relevant_records(paths: Iterable[Path], since: str) -> Iterable[dict[str, Any]]:
    keep = {
        "decision",
        "health_transition",
        "initial_broker_snapshot",
        "robinhood_snapshot",
        "pre_order_broker_snapshot",
        "broker_order_preview",
        "broker_order_terminal",
    }
    since_date = datetime.fromisoformat(since).date()
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("type") not in keep:
                    continue
                if _timestamp(record).astimezone(EASTERN).date() < since_date:
                    continue
                yield record


def calculate(records: Iterable[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    evaluations: Counter[str] = Counter()
    halted_days: set[str] = set()
    broker_snapshot_days: set[str] = set()
    clear_previews = 0
    alerted_previews = 0
    bad_terminals = 0

    for record in records:
        day = _timestamp(record).astimezone(EASTERN).date().isoformat()
        kind = record["type"]
        payload = record.get("payload", {})
        if kind == "decision":
            evaluations[day] += 1
        elif kind == "health_transition" and payload.get("state") == "halted":
            halted_days.add(day)
        elif kind in {
            "initial_broker_snapshot",
            "robinhood_snapshot",
            "pre_order_broker_snapshot",
        }:
            broker_snapshot_days.add(day)
        elif kind == "broker_order_preview":
            preview = payload.get("preview", {})
            if preview.get("alerts"):
                alerted_previews += 1
            else:
                clear_previews += 1
        elif kind == "broker_order_terminal":
            state = str(payload.get("result", {}).get("state", "")).lower()
            if state not in {"filled", "cancelled"}:
                bad_terminals += 1

    full_sessions = sorted(
        day
        for day, count in evaluations.items()
        if count >= MIN_EVALUATIONS_PER_SESSION
    )
    clean_sessions = [day for day in full_sessions if day not in halted_days]
    checks = {
        "clean_shadow_or_preview_sessions": (
            len(clean_sessions) >= REQUIRED_CLEAN_SESSIONS
        ),
        "clear_broker_previews": clear_previews >= REQUIRED_CLEAR_PREVIEWS,
        "no_preview_alerts": alerted_previews == 0,
        "broker_reconciliation_observed": bool(broker_snapshot_days),
        "no_failed_live_terminals": bad_terminals == 0,
        "live_time_lock_reached": (
            datetime.now().astimezone().timestamp()
            >= datetime.fromisoformat(
                settings.live_not_before.replace("Z", "+00:00")
            ).timestamp()
        ),
    }
    return {
        "ready_for_live": all(checks.values()),
        "checks": checks,
        "clean_sessions": clean_sessions,
        "full_session_evaluations": {
            day: evaluations[day] for day in sorted(evaluations)
        },
        "halted_days": sorted(halted_days),
        "broker_snapshot_days": sorted(broker_snapshot_days),
        "clear_previews": clear_previews,
        "alerted_previews": alerted_previews,
        "required_clean_sessions": REQUIRED_CLEAN_SESSIONS,
        "required_clear_previews": REQUIRED_CLEAR_PREVIEWS,
        "live_not_before": settings.live_not_before,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-07-27")
    parser.add_argument(
        "--ledger", type=Path, default=Path("swagger_state/ledger.jsonl")
    )
    parser.add_argument("--archive-dir", type=Path, default=None)
    args = parser.parse_args()
    settings = Settings.from_env()
    paths = ledger_paths(args.ledger, args.archive_dir)
    result = calculate(relevant_records(paths, args.since), settings)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready_for_live"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
