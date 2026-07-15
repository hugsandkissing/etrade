"""Generate daily or cumulative shadow-performance reports from the audit ledger."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


def _return_pct(first: float | None, last: float | None) -> float | None:
    if first is None or last is None or first == 0:
        return None
    return (last - first) / first * 100


def calculate(records: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [r for r in records if r["type"] == "decision"]
    verdicts = [r for r in records if r["type"] == "risk_verdict"]
    fills = [r["payload"] for r in records if r["type"] == "shadow_fill"]
    snapshots = [r["payload"] for r in records if r["type"] == "shadow_snapshot"]
    realized = [
        float(fill.get("realized_pnl", 0))
        for fill in fills
        if fill.get("action") == "SELL"
    ]
    winners = [pnl for pnl in realized if pnl > 0]
    losers = [pnl for pnl in realized if pnl < 0]

    equity_curve = [float(item["value"]) for item in snapshots]
    peak = None
    max_drawdown = 0.0
    for value in equity_curve:
        peak = value if peak is None else max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value - peak) / peak * 100)

    benchmark_prices: dict[str, list[float]] = defaultdict(list)
    for record in records:
        payload = record.get("payload", {})
        if record["type"] == "market_event" and payload.get("event_type") == "bar":
            if (
                payload.get("symbol") in {"VG", "SPY"}
                and payload.get("price") is not None
            ):
                benchmark_prices[payload["symbol"]].append(float(payload["price"]))

    open_buys: dict[str, deque[tuple[datetime, float]]] = defaultdict(deque)
    holding_seconds: list[float] = []
    for fill in fills:
        timestamp = datetime.fromisoformat(fill["timestamp"].replace("Z", "+00:00"))
        quantity = float(fill["quantity"])
        symbol = fill["symbol"]
        if fill["action"] == "BUY":
            open_buys[symbol].append((timestamp, quantity))
        elif fill["action"] == "SELL":
            remaining = quantity
            while remaining > 1e-12 and open_buys[symbol]:
                opened, available = open_buys[symbol][0]
                matched = min(remaining, available)
                holding_seconds.append((timestamp - opened).total_seconds())
                remaining -= matched
                if matched >= available - 1e-12:
                    open_buys[symbol].popleft()
                else:
                    open_buys[symbol][0] = (opened, available - matched)

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    report = {
        "total_proposals": len(decisions),
        "action_counts": {
            action: sum(
                1 for item in decisions if item["payload"].get("action") == action
            )
            for action in ("HOLD", "BUY", "SELL", "ROTATE")
        },
        "accepted_proposals": sum(
            1 for item in verdicts if item["payload"].get("approved")
        ),
        "rejected_proposals": sum(
            1 for item in verdicts if not item["payload"].get("approved")
        ),
        "hypothetical_fills": len(fills),
        "closed_trades": len(realized),
        "win_rate_pct": (len(winners) / len(realized) * 100) if realized else None,
        "average_winner": mean(winners) if winners else None,
        "average_loser": mean(losers) if losers else None,
        "expectancy": mean(realized) if realized else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else None,
        "maximum_drawdown_pct": max_drawdown,
        "average_holding_hours": mean(holding_seconds) / 3600
        if holding_seconds
        else None,
        "spread_cost": sum(float(fill.get("spread_cost", 0)) for fill in fills),
        "slippage_cost": sum(float(fill.get("slippage_cost", 0)) for fill in fills),
        "shadow_return_pct": _return_pct(equity_curve[0], equity_curve[-1])
        if equity_curve
        else None,
        "vg_buy_and_hold_pct": _return_pct(
            benchmark_prices["VG"][0] if benchmark_prices["VG"] else None,
            benchmark_prices["VG"][-1] if benchmark_prices["VG"] else None,
        ),
        "spy_buy_and_hold_pct": _return_pct(
            benchmark_prices["SPY"][0] if benchmark_prices["SPY"] else None,
            benchmark_prices["SPY"][-1] if benchmark_prices["SPY"] else None,
        ),
    }
    return report


def load_records(path: Path, date: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
    if date:
        records = [record for record in records if record["timestamp"].startswith(date)]
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--date", help="UTC date in YYYY-MM-DD format")
    scope.add_argument("--cumulative", action="store_true")
    parser.add_argument(
        "--ledger", type=Path, default=Path("swagger_state/ledger.jsonl")
    )
    args = parser.parse_args()
    records = load_records(args.ledger, None if args.cumulative else args.date)
    print(json.dumps(calculate(records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
