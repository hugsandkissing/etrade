from datetime import datetime, timezone

from swagger.ledger import AuditLedger
from swagger.broker import BrokerCapabilities, plan_allocation_order
from swagger.models import (
    AccountState,
    Action,
    Decision,
    Quote,
    TargetAllocation,
)
from swagger.shadow import ShadowPortfolio


def make_decision(action, key, amount=None, quantity=None, target=None):
    return Decision(
        action=action,
        symbol="VG",
        timestamp=datetime.now(timezone.utc),
        confidence=0.8,
        triggering_signals=("a", "b"),
        rationale="test",
        maximum_dollar_amount=amount,
        share_quantity=quantity,
        expected_holding_period="test",
        invalidation_condition="test",
        suggested_protective_exit_pct=-7,
        idempotency_key=key,
        target_allocations=(TargetAllocation("VG", target),)
        if target is not None
        else (),
        target_is_complete=target is not None,
    )


def test_ledger_is_append_only_and_hash_chained(tmp_path):
    ledger = AuditLedger(tmp_path / "ledger.jsonl")
    first = ledger.append("decision", {"idempotency_key": "one"})
    ledger.correction(first["sequence"], "test correction", {"value": 2})
    records = list(ledger.records())
    assert len(records) == 2
    assert records[0]["payload"] == {"idempotency_key": "one"}
    assert records[1]["type"] == "correction"
    assert ledger.verify_chain() == (True, "ok")
    assert ledger.contains_idempotency_key("one")


def test_shadow_fills_use_ask_for_buys_and_bid_for_sells(tmp_path):
    portfolio = ShadowPortfolio(tmp_path / "state.json", 50, slippage_bps=10)
    quote = Quote("VG", 9.99, 10.01, datetime.now(timezone.utc), "test")
    buy = portfolio.execute(make_decision(Action.BUY, "buy", amount=20), quote)
    assert buy.price > quote.ask
    position = portfolio.position("VG")
    assert position is not None
    sell = portfolio.execute(
        make_decision(Action.SELL, "sell", quantity=position.quantity), quote
    )
    assert sell.price < quote.bid
    assert portfolio.position("VG") is None
    assert sell.spread_cost > 0
    assert sell.slippage_cost > 0


def test_shadow_state_recovers_after_restart(tmp_path):
    path = tmp_path / "state.json"
    first = ShadowPortfolio(path, 50, slippage_bps=0)
    quote = Quote("VG", 10, 10.01, datetime.now(timezone.utc), "test")
    first.execute(make_decision(Action.BUY, "buy", amount=20), quote)
    recovered = ShadowPortfolio(path, 50, slippage_bps=0)
    assert recovered.position("VG") is not None
    assert recovered.cash == first.cash


def test_fractional_target_allocation_uses_nearly_all_target_dollars(tmp_path):
    portfolio = ShadowPortfolio(tmp_path / "state.json", 50, slippage_bps=0)
    quote = Quote("VG", 13.20, 13.22, datetime.now(timezone.utc), "test")
    fill = portfolio.execute(
        make_decision(Action.BUY, "allocation-buy", amount=50, target=1.0), quote
    )
    assert fill.quantity % 1 != 0
    assert fill.target_weight == 1.0
    assert fill.execution_residual_cash_after is not None
    assert fill.execution_residual_cash_after < 0.01


def test_whole_share_adapter_rounds_and_reports_residual_cash():
    quote = Quote("VG", 13.20, 13.22, datetime.now(timezone.utc), "test")
    order = plan_allocation_order(
        target=TargetAllocation("VG", 1.0),
        account=AccountState(value=50, buying_power=50),
        quote=quote,
        capabilities=BrokerCapabilities(supports_fractional_shares=False),
    )
    assert order is not None
    assert order.quantity == 3
    assert round(order.residual_cash, 2) == 10.34


def test_allocation_snapshot_reports_target_realized_drift_and_cash(tmp_path):
    portfolio = ShadowPortfolio(tmp_path / "state.json", 50, slippage_bps=0)
    quote = Quote("VG", 10, 10, datetime.now(timezone.utc), "test")
    portfolio.execute(make_decision(Action.BUY, "half", amount=25, target=0.5), quote)
    snapshot = portfolio.allocation_snapshot(
        {"VG": quote}, (TargetAllocation("VG", 0.5),)
    )
    assert snapshot.lines[0].target_weight == 0.5
    assert snapshot.lines[0].realized_weight == 0.5
    assert snapshot.lines[0].drift_weight == 0
    assert snapshot.target_cash_value == 25
    assert snapshot.realized_cash_value == 25
    assert snapshot.target_cash_weight == 0.5
    assert snapshot.realized_cash_weight == 0.5
    assert snapshot.execution_residual_cash == 0


def test_allocation_drift_is_target_minus_realized(tmp_path):
    portfolio = ShadowPortfolio(tmp_path / "state.json", 50, slippage_bps=0)
    quote = Quote("VG", 10, 10, datetime.now(timezone.utc), "test")
    portfolio.execute(
        make_decision(Action.BUY, "quarter", amount=12.5, target=0.25), quote
    )
    snapshot = portfolio.allocation_snapshot(
        {"VG": quote}, (TargetAllocation("VG", 0.5),)
    )
    assert snapshot.lines[0].drift_weight == 0.25


def test_entry_is_chunked_but_protective_exit_sells_full_fractional_position(
    tmp_path,
):
    portfolio = ShadowPortfolio(
        tmp_path / "state.json",
        50,
        slippage_bps=0,
        max_order_notional=10,
        max_capital=50,
    )
    quote = Quote("VG", 10, 10, datetime.now(timezone.utc), "test")
    buy = portfolio.execute(
        make_decision(Action.BUY, "chunked-buy", amount=10, target=1.0),
        quote,
    )
    assert buy.gross_value == 10
    held = portfolio.position("VG")
    assert held is not None
    sell = portfolio.execute(
        make_decision(Action.SELL, "full-exit", quantity=held.quantity, target=0.0),
        quote,
    )
    assert sell.quantity == held.quantity
    assert portfolio.position("VG") is None


def test_ledger_rotates_without_breaking_hash_continuity(tmp_path):
    path = tmp_path / "ledger.jsonl"
    archive_dir = tmp_path / "archive"
    ledger = AuditLedger(path, max_bytes=512, archive_dir=archive_dir)
    ledger.append("large", {"value": "x" * 600})
    ledger.append("after_rotation", {"idempotency_key": "rotated"})

    archives = list(archive_dir.glob("*.jsonl"))
    assert len(archives) == 1
    records = list(ledger.records())
    assert records[0]["type"] == "ledger_segment_started"
    assert records[0]["payload"]["archived_file"] == archives[0].name
    assert ledger.verify_chain() == (True, "ok")
    assert ledger.contains_idempotency_key("rotated")
