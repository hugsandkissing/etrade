from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from swagger.config import ConfigurationError, Settings
from swagger.models import (
    Action,
    AccountState,
    Decision,
    HealthState,
    Position,
    Quote,
    TargetAllocation,
)
from swagger.risk import RiskContext, RiskKernel


def decision(action=Action.BUY, symbol="VG", amount=20.0):
    return Decision(
        action=action,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        confidence=0.8,
        triggering_signals=("session_high_cross", "relative_volume"),
        rationale="test",
        maximum_dollar_amount=amount if action is Action.BUY else None,
        share_quantity=1.0 if action is Action.SELL else None,
        expected_holding_period="test",
        invalidation_condition="test",
        suggested_protective_exit_pct=-7.0,
        idempotency_key="test-key",
        target_allocations=(
            TargetAllocation(symbol, amount / 50.0)
            if action is Action.BUY
            else TargetAllocation(symbol, 0.0),
        ),
        target_is_complete=True,
    )


def context(settings, *, account_value=50.0, quote=None, daily_pnl=0.0):
    quote = quote or Quote("VG", 10.00, 10.01, datetime.now(timezone.utc), "test")
    account = AccountState(account_value, 50.0, daily_pnl_pct=daily_pnl)
    return RiskContext(account, quote, HealthState.HEALTHY, True)


def test_live_mode_is_impossible():
    with pytest.raises(ConfigurationError, match="Only SWAGGER_MODE=shadow"):
        replace(Settings(), mode="live").validate(require_market_data=False)


def test_unknown_broker_is_impossible():
    with pytest.raises(ConfigurationError, match="mock or robinhood_readonly"):
        replace(Settings(), broker_mode="robinhood").validate(require_market_data=False)


def test_readonly_robinhood_is_allowed_but_live_mode_is_not():
    replace(
        Settings(),
        broker_mode="robinhood_readonly",
    ).validate(require_market_data=False)


def test_buy_blocked_at_floor_but_sell_remains_possible():
    settings = Settings()
    kernel = RiskKernel(settings)
    assert not kernel.evaluate(decision(), context(settings, account_value=40)).approved
    quote = Quote("VG", 10, 10.01, datetime.now(timezone.utc), "test")
    sell_context = RiskContext(
        AccountState(40, 30, positions=(Position("VG", 1, 10),)),
        quote,
        HealthState.HEALTHY,
        True,
    )
    assert kernel.evaluate(decision(Action.SELL), sell_context).approved


def test_stale_quote_and_wide_spread_are_blocked():
    settings = Settings(stale_seconds=5, max_spread_pct=0.5)
    stale = Quote(
        "VG", 9.0, 10.0, datetime.now(timezone.utc) - timedelta(seconds=10), "test"
    )
    verdict = RiskKernel(settings).evaluate(decision(), context(settings, quote=stale))
    assert not verdict.approved
    assert "execution-side quote is stale" in verdict.reasons
    assert "bid/ask spread exceeds configured maximum" in verdict.reasons


def test_duplicate_and_unhealthy_engine_are_blocked():
    settings = Settings()
    base = context(settings)
    blocked = replace(base, health=HealthState.DEGRADED, duplicate_idempotency_key=True)
    verdict = RiskKernel(settings).evaluate(decision(), blocked)
    assert not verdict.approved
    assert "duplicate idempotency key" in verdict.reasons


def test_complete_target_is_authoritative_over_legacy_amount():
    settings = Settings()
    proposal = replace(
        decision(amount=999),
        target_allocations=(TargetAllocation("VG", 0.5),),
        target_is_complete=True,
    )
    verdict = RiskKernel(settings).evaluate(proposal, context(settings))
    assert verdict.approved


def test_complete_target_must_preserve_existing_positions():
    settings = Settings()
    proposal = replace(
        decision(),
        target_allocations=(TargetAllocation("VG", 0.5),),
        target_is_complete=True,
    )
    quote = Quote("VG", 10, 10.01, datetime.now(timezone.utc), "test")
    account = AccountState(
        50,
        20,
        positions=(Position("QQQ", 0.1, 300),),
    )
    verdict = RiskKernel(settings).evaluate(
        proposal, RiskContext(account, quote, HealthState.HEALTHY, True)
    )
    assert not verdict.approved
    assert "complete target omits an existing position" in verdict.reasons
