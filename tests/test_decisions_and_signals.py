import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from swagger.bar_aggregator import SignalAggregator
from swagger.config import Settings
from swagger.decision_engine import DecisionContext, RuleBasedDecisionProvider
from swagger.models import (
    Action,
    EventType,
    MarketEvent,
    Position,
    Quote,
    Signal,
    TargetAllocation,
)


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
    assert first.target_allocations[0].symbol == "VG"
    assert first.target_allocations[0].weight == 0.5
    assert first.target_is_complete
    assert second.action is Action.HOLD
    assert "cooldown" in second.rationale.lower()


def test_buy_fails_closed_when_funded_capital_is_fully_deployed():
    provider = RuleBasedDecisionProvider(Settings(max_capital=50))
    result = asyncio.run(
        provider.propose(
            replace(
                context(
                    [
                        signal("session_high_cross", "price"),
                        signal("relative_volume", "volume"),
                    ]
                ),
                invested_cost=50,
            )
        )
    )
    assert result.action is Action.HOLD
    assert "no room" in result.rationale


def test_sell_target_preserves_other_portfolio_allocations():
    provider = RuleBasedDecisionProvider(Settings())
    sell_context = DecisionContext(
        symbol="VG",
        timestamp=NOW,
        signals=(
            signal("confirmed_stop", "risk", "bearish"),
            signal("session_low_cross", "price", "bearish"),
        ),
        quote=Quote("VG", 10, 10.01, NOW, "test"),
        position=Position("VG", 1, 11),
        buying_power=10,
        account_value=50,
        current_allocations=(
            TargetAllocation("QQQ", 0.3),
            TargetAllocation("VG", 0.5),
        ),
    )
    result = asyncio.run(provider.propose(sell_context))
    targets = {item.symbol: item.weight for item in result.target_allocations}
    assert result.action is Action.SELL
    assert result.target_is_complete
    assert targets == {"QQQ": 0.3, "VG": 0.0}


def test_account_floor_signal_proposes_immediate_full_exit_target():
    provider = RuleBasedDecisionProvider(Settings())
    floor_context = DecisionContext(
        symbol="VG",
        timestamp=NOW,
        signals=(signal("account_floor_threat", "risk", "bearish"),),
        quote=Quote("VG", 10, 10.01, NOW, "test"),
        position=Position("VG", 3.5, 13),
        buying_power=0,
        account_value=40,
        invested_cost=45.5,
        current_allocations=(TargetAllocation("VG", 1.0),),
    )
    result = asyncio.run(provider.propose(floor_context))
    assert result.action is Action.SELL
    assert result.target_allocations == (TargetAllocation("VG", 0.0),)
    assert "floor" in result.rationale.lower()


def test_buy_target_uses_remaining_weight_without_floating_overallocation():
    provider = RuleBasedDecisionProvider(Settings())
    buy_context = replace(
        context(
            [
                signal("session_high_cross", "price"),
                signal("relative_volume", "volume"),
            ]
        ),
        symbol="QQQ",
        buying_power=24.533316604200696,
        account_value=49.501412,
        current_allocations=(TargetAllocation("VG", 0.5043918550047808),),
    )
    result = asyncio.run(provider.propose(buy_context))
    assert result.action is Action.BUY
    assert sum(item.weight for item in result.target_allocations) <= 1.0


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
