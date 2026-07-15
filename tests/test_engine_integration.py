import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from swagger.config import Settings
from swagger.engine import ShadowEngine
from swagger.models import EventType, HealthState, MarketEvent


def test_engine_creates_only_a_hypothetical_fill(tmp_path):
    settings = replace(
        Settings(),
        alpaca_api_key="unused-test-key",
        alpaca_api_secret="unused-test-secret",
        ledger_path=tmp_path / "ledger.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "KILL_SWITCH",
        min_five_minute_volume=100,
        stale_seconds=86_400,
    )
    engine = ShadowEngine(settings)
    engine.health.set(HealthState.HEALTHY, "synthetic integration test")
    now = datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc)

    events = [
        MarketEvent("q1", EventType.QUOTE, "VG", now, bid=10.00, ask=10.01),
        MarketEvent("b1", EventType.BAR, "VG", now, price=10.00, volume=100),
        MarketEvent(
            "b2",
            EventType.BAR,
            "VG",
            now + timedelta(minutes=1),
            price=10.10,
            volume=100,
        ),
        MarketEvent(
            "b3",
            EventType.BAR,
            "VG",
            now + timedelta(minutes=2),
            price=10.20,
            volume=300,
        ),
    ]

    async def feed():
        for event in events:
            await engine._handle_event(event)
        await engine._handle_event(events[0])

    asyncio.run(feed())
    records = list(engine.ledger.records())
    fills = [record for record in records if record["type"] == "shadow_fill"]
    assert len(fills) == 1
    assert fills[0]["payload"]["action"] == "BUY"
    assert engine.portfolio.position("VG") is not None
    assert sum(record["type"] == "market_event" for record in records) == len(events)
    assert not any(
        record["type"] in {"order_preview", "order_placed"} for record in records
    )
