from swagger.config import Settings
from swagger.market_stream import AlpacaWebSocketStream
from swagger.models import EventType
from swagger.report import calculate


def test_alpaca_parser_and_duplicate_identity():
    stream = AlpacaWebSocketStream(Settings())
    item = {"T": "q", "S": "VG", "bp": 10.0, "ap": 10.01, "t": "2026-07-14T14:00:00Z"}
    event = stream._parse(item)
    duplicate = stream._parse(dict(item))
    assert event is not None
    assert event.event_type is EventType.QUOTE
    assert event.event_id == duplicate.event_id


def test_report_calculates_costs_and_benchmarks():
    records = [
        {"type": "decision", "payload": {"action": "BUY"}},
        {"type": "risk_verdict", "payload": {"approved": True}},
        {
            "type": "shadow_fill",
            "payload": {
                "action": "BUY",
                "symbol": "VG",
                "quantity": 1,
                "price": 10,
                "spread_cost": 0.01,
                "slippage_cost": 0.02,
                "timestamp": "2026-07-14T14:00:00Z",
                "realized_pnl": 0,
            },
        },
        {
            "type": "shadow_fill",
            "payload": {
                "action": "SELL",
                "symbol": "VG",
                "quantity": 1,
                "price": 11,
                "spread_cost": 0.01,
                "slippage_cost": 0.02,
                "timestamp": "2026-07-14T15:00:00Z",
                "realized_pnl": 1,
            },
        },
        {"type": "shadow_snapshot", "payload": {"value": 50}},
        {"type": "shadow_snapshot", "payload": {"value": 51}},
        {
            "type": "market_event",
            "payload": {"event_type": "bar", "symbol": "VG", "price": 10},
        },
        {
            "type": "market_event",
            "payload": {"event_type": "bar", "symbol": "VG", "price": 11},
        },
    ]
    report = calculate(records)
    assert report["hypothetical_fills"] == 2
    assert report["win_rate_pct"] == 100
    assert round(report["shadow_return_pct"], 2) == 2
    assert round(report["vg_buy_and_hold_pct"], 2) == 10
    assert report["average_holding_hours"] == 1
