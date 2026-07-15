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
    allocation_snapshots = [
        r["payload"] for r in records if r["type"] == "allocation_snapshot"
    ]
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
    actionable = [
        item
        for item in decisions
        if item.get("payload", {}).get("action") in {"BUY", "SELL", "ROTATE"}
    ]
    latest_allocation = allocation_snapshots[-1] if allocation_snapshots else {}
    report = {
        "total_evaluations": len(decisions),
        "actionable_proposals": len(actionable),
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
        "latest_target_allocation": (
            {
                line["symbol"]: line["target_weight"]
                for line in latest_allocation.get("lines", [])
            }
            if allocation_snapshots
            else {}
        ),
        "latest_realized_allocation": (
            {
                line["symbol"]: line["realized_weight"]
                for line in latest_allocation.get("lines", [])
            }
            if allocation_snapshots
            else {}
        ),
        "latest_allocation_drift": (
            {
                line["symbol"]: line["drift_weight"]
                for line in latest_allocation.get("lines", [])
            }
            if allocation_snapshots
            else {}
        ),
        "latest_cash": latest_allocation.get("cash"),
        "latest_target_cash_weight": latest_allocation.get("target_cash_weight"),
        "latest_realized_cash_weight": latest_allocation.get("realized_cash_weight"),
        "latest_target_cash_value": latest_allocation.get("target_cash_value"),
        "latest_realized_cash_value": latest_allocation.get("realized_cash_value"),
        "latest_execution_residual_cash": latest_allocation.get(
            "execution_residual_cash"
        ),
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
    relevant_types = {
        "decision",
        "risk_verdict",
        "shadow_fill",
        "shadow_snapshot",
        "allocation_snapshot",
        "market_event",
    }
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            # Legacy ledgers contain millions of raw quotes and trades. Avoid
            # decoding them; only bars are needed for benchmark calculation.
            if '"type": "market_event"' in line and '"event_type": "bar"' not in line:
                continue
            record = json.loads(line)
            if record.get("type") not in relevant_types:
                continue
            if date and not record.get("timestamp", "").startswith(date):
                continue
            records.append(record)
    return records


def ledger_paths(active: Path, archive_dir: Path | None = None) -> list[Path]:
    archive_dir = archive_dir or active.parent / "ledger_archive"
    archives = (
        sorted(archive_dir.glob("ledger-*.jsonl")) if archive_dir.exists() else []
    )
    return [*archives, active]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--date", help="UTC date in YYYY-MM-DD format")
    scope.add_argument("--cumulative", action="store_true")
    parser.add_argument(
        "--ledger", type=Path, default=Path("swagger_state/ledger.jsonl")
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="ledger archive directory (defaults beside the active ledger)",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="exclude rotated archives from this report",
    )
    args = parser.parse_args()
    paths = (
        [args.ledger]
        if args.active_only
        else ledger_paths(args.ledger, args.archive_dir)
    )
    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(load_records(path, None if args.cumulative else args.date))
    print(json.dumps(calculate(records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
