from datetime import datetime, timezone

from swagger.ledger import AuditLedger
from swagger.models import Action, Decision, Quote
from swagger.shadow import ShadowPortfolio


def make_decision(action, key, amount=None, quantity=None):
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
