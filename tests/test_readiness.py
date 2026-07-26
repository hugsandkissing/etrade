from dataclasses import replace

from swagger.config import Settings
from swagger.readiness import calculate


def record(kind, timestamp, payload=None):
    return {
        "type": kind,
        "timestamp": timestamp,
        "payload": payload or {},
    }


def test_readiness_requires_sessions_previews_and_broker_reconciliation():
    records = []
    for day in range(1, 11):
        timestamp = f"2026-08-{day:02d}T15:00:00Z"
        records.extend(record("decision", timestamp) for _ in range(100))
    records.append(record("robinhood_snapshot", "2026-08-10T15:00:00Z"))
    for index in range(3):
        records.append(
            record(
                "broker_order_preview",
                f"2026-08-10T15:0{index}:00Z",
                {"preview": {"alerts": []}},
            )
        )
    settings = replace(Settings(), live_not_before="2020-01-01T00:00:00Z")
    result = calculate(records, settings)
    assert result["ready_for_live"]


def test_halt_and_preview_alert_fail_readiness():
    records = [
        record("decision", "2026-08-01T15:00:00Z")
        for _ in range(100)
    ]
    records.extend(
        [
            record(
                "health_transition",
                "2026-08-01T15:30:00Z",
                {"state": "halted"},
            ),
            record(
                "broker_order_preview",
                "2026-08-01T15:31:00Z",
                {"preview": {"alerts": ["warning"]}},
            ),
        ]
    )
    result = calculate(
        records, replace(Settings(), live_not_before="2020-01-01T00:00:00Z")
    )
    assert not result["ready_for_live"]
    assert result["clean_sessions"] == []
    assert result["alerted_previews"] == 1
