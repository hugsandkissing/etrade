import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from swagger.config import Settings
from swagger.engine import ShadowEngine
from swagger.models import EventType, HealthState, MarketEvent
from swagger.models import (
    Action,
    AccountState,
    BrokerOrderPreview,
    Decision,
    Quote,
    TargetAllocation,
)
from swagger.robinhood_mcp import RobinhoodReadOnlySnapshot


def test_engine_creates_only_a_hypothetical_fill(tmp_path):
    settings = replace(
        Settings(),
        alpaca_api_key="unused-test-key",
        alpaca_api_secret="unused-test-secret",
        ledger_path=tmp_path / "ledger.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "KILL_SWITCH",
        min_five_minute_volume=100,
        # Synthetic events use a fixed date; keep quote freshness out of this
        # allocation-flow integration test.
        stale_seconds=30 * 86_400,
    )
    engine = ShadowEngine(settings)
    engine.health.set(HealthState.HEALTHY, "synthetic integration test")
    now = datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc)

    events = [
        MarketEvent("q1", EventType.QUOTE, "VG", now, bid=10.00, ask=10.01),
        MarketEvent(
            "q2",
            EventType.QUOTE,
            "VG",
            now + timedelta(seconds=1),
            bid=10.00,
            ask=10.01,
        ),
        MarketEvent(
            "t1",
            EventType.TRADE,
            "VG",
            now + timedelta(seconds=2),
            price=10.01,
        ),
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
    market_events = [
        record["payload"] for record in records if record["type"] == "market_event"
    ]
    assert len(market_events) == 4
    assert sum(item["event_type"] == "quote" for item in market_events) == 1
    assert not any(item["event_type"] == "trade" for item in market_events)
    assert not any(
        record["type"] in {"order_preview", "order_placed"} for record in records
    )


def test_engine_recovers_health_when_market_data_resumes(tmp_path):
    settings = replace(
        Settings(),
        alpaca_api_key="unused-test-key",
        alpaca_api_secret="unused-test-secret",
        ledger_path=tmp_path / "ledger.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "KILL_SWITCH",
    )
    engine = ShadowEngine(settings)
    engine.health.set(HealthState.DEGRADED, "market data is stale")
    event = MarketEvent(
        "resume",
        EventType.QUOTE,
        "VG",
        datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
        bid=10,
        ask=10.01,
    )
    asyncio.run(engine._handle_event(event))
    assert engine.health.state is HealthState.HEALTHY


def test_preview_mode_reviews_but_never_places_an_order(tmp_path):
    settings = replace(
        Settings(),
        mode="preview",
        broker_mode="robinhood_preview",
        alpaca_api_key="unused-test-key",
        alpaca_api_secret="unused-test-secret",
        notifications_enabled=False,
        ledger_path=tmp_path / "ledger.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "KILL_SWITCH",
    )
    engine = ShadowEngine(settings)
    engine.health.set(HealthState.HEALTHY, "test")
    now = datetime.now(timezone.utc)
    quote = Quote("VG", 10.0, 10.01, now, "robinhood-mcp")
    snapshot = RobinhoodReadOnlySnapshot(
        account_masked="••••4444",
        account_type="individual",
        portfolio_value=50.0,
        buying_power=50.0,
        positions=(),
        open_order_symbols=(),
        quotes=(quote,),
        tradability={"VG": True},
        fractional_tradability={"VG": True},
        verified_at=now,
    )

    class PreviewOnlyBroker:
        def __init__(self):
            self.reviewed = []

        async def review_order(self, order, *, ref_id=None):
            self.reviewed.append(order)
            return BrokerOrderPreview(
                ref_id=ref_id,
                symbol=order.symbol,
                side="buy",
                quantity="0.999",
                estimated_notional=order.estimated_value,
                alerts=(),
            )

    broker = PreviewOnlyBroker()
    engine.robinhood = broker
    engine._broker_snapshot = snapshot
    decision = Decision(
        action=Action.BUY,
        symbol="VG",
        timestamp=now,
        confidence=0.8,
        triggering_signals=("session_high_cross", "relative_volume"),
        rationale="test",
        maximum_dollar_amount=10.0,
        share_quantity=None,
        expected_holding_period="test",
        invalidation_condition="test",
        suggested_protective_exit_pct=-7.0,
        idempotency_key="preview-only",
        target_allocations=(TargetAllocation("VG", 0.5),),
        target_is_complete=True,
    )

    asyncio.run(
        engine._handle_broker_decision(
            decision,
            AccountState(50.0, 50.0, verified_at=now),
            quote,
        )
    )
    assert len(broker.reviewed) == 1
    assert broker.reviewed[0].estimated_value <= settings.max_order_notional
    types = [record["type"] for record in engine.ledger.records()]
    assert "broker_order_preview" in types
    assert "broker_order_submitted" not in types
