import asyncio
from datetime import datetime, timedelta, timezone

from swagger.bar_aggregator import SignalAggregator
from swagger.config import Settings
from swagger.decision_engine import DecisionContext, RuleBasedDecisionProvider
from swagger.models import Action, EventType, MarketEvent, Quote, Signal


NOW = datetime.now(timezone.utc)


def signal(name, family, direction="bullish"):
    return Signal(name, family, "VG", NOW, direction, 0.8)


def context(signals):
    return DecisionContext(
        symbol="VG",
        timestamp=NOW,
        signals=tuple(signals),
        quote=Quote("VG", 10, 10.01, NOW, "test"),
        position=None,
        buying_power=50,
        account_value=50,
    )


def test_two_signals_from_same_family_do_not_confirm_buy():
    provider = RuleBasedDecisionProvider(Settings())
    result = asyncio.run(
        provider.propose(
            context(
                [
                    signal("session_high_cross", "price"),
                    signal("position_move", "price"),
                ]
            )
        )
    )
    assert result.action is Action.HOLD


def test_two_distinct_families_confirm_buy_and_cooldown():
    provider = RuleBasedDecisionProvider(Settings(proposal_cooldown_seconds=300))
    first = asyncio.run(
        provider.propose(
            context(
                [
                    signal("session_high_cross", "price"),
                    signal("relative_volume", "volume"),
                ]
            )
        )
    )
    second = asyncio.run(
        provider.propose(
            context(
                [
                    signal("session_high_cross", "price"),
                    signal("relative_volume", "volume"),
                ]
            )
        )
    )
    assert first.action is Action.BUY
    assert second.action is Action.HOLD
    assert "cooldown" in second.rationale.lower()


def test_aggregator_emits_price_and_volume_families():
    aggregator = SignalAggregator(Settings(min_five_minute_volume=100))
    aggregator.process(MarketEvent("1", EventType.BAR, "VG", NOW, price=10, volume=100))
    aggregator.process(
        MarketEvent(
            "2", EventType.BAR, "VG", NOW + timedelta(minutes=1), price=10.1, volume=100
        )
    )
    signals = aggregator.process(
        MarketEvent(
            "3", EventType.BAR, "VG", NOW + timedelta(minutes=2), price=10.2, volume=300
        )
    )
    assert {item.family for item in signals} >= {"price", "volume"}


def test_aggregator_detects_stale_data():
    aggregator = SignalAggregator(Settings(stale_seconds=5))
    aggregator.process(MarketEvent("1", EventType.TRADE, "VG", NOW, price=10))
    assert aggregator.stale(NOW + timedelta(seconds=6))
