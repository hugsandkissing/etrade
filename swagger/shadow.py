"""Durable hypothetical portfolio accounting, separate from any real broker."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Action, AccountState, Decision, Position, Quote, ShadowFill


class ShadowPortfolioError(RuntimeError):
    pass


class ShadowPortfolio:
    def __init__(self, path: Path, starting_cash: float, slippage_bps: float):
        self.path = path
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.cash = starting_cash
        self.positions: dict[str, dict[str, float]] = {}
        self.fills: list[dict] = []
        self.session_start_value = starting_cash
        self.session_date = self._session_date()
        self._load()

    @staticmethod
    def _session_date() -> str:
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.cash = float(data["cash"])
        self.positions = data.get("positions", {})
        self.fills = data.get("fills", [])
        self.session_start_value = float(
            data.get("session_start_value", self.starting_cash)
        )
        if data.get("session_date") != self.session_date:
            self.session_start_value = self.cash + sum(
                float(item["quantity"]) * float(item["average_cost"])
                for item in self.positions.values()
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "starting_cash": self.starting_cash,
            "session_start_value": self.session_start_value,
            "session_date": self.session_date,
            "cash": self.cash,
            "positions": self.positions,
            "fills": self.fills,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        fd, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=".shadow-", text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def position(self, symbol: str) -> Position | None:
        item = self.positions.get(symbol.upper())
        if not item:
            return None
        return Position(
            symbol.upper(), float(item["quantity"]), float(item["average_cost"])
        )

    def account_state(self, quotes: dict[str, Quote] | None = None) -> AccountState:
        quotes = quotes or {}
        value = self.cash
        positions: list[Position] = []
        for symbol, item in self.positions.items():
            position = Position(
                symbol, float(item["quantity"]), float(item["average_cost"])
            )
            positions.append(position)
            quote = quotes.get(symbol)
            value += position.quantity * (
                quote.midpoint if quote else position.average_cost
            )
        daily_pnl = (
            0
            if self.session_start_value <= 0
            else (value - self.session_start_value) / self.session_start_value * 100
        )
        return AccountState(
            value=round(value, 6),
            buying_power=round(self.cash, 6),
            positions=tuple(positions),
            daily_pnl_pct=round(daily_pnl, 6),
        )

    def execute(self, decision: Decision, quote: Quote) -> ShadowFill:
        if decision.action not in {Action.BUY, Action.SELL}:
            raise ShadowPortfolioError(f"cannot shadow-fill {decision.action.value}")
        slippage_rate = self.slippage_bps / 10_000
        midpoint = quote.midpoint
        realized_pnl = 0.0

        if decision.action is Action.BUY:
            base_price = quote.ask
            fill_price = base_price * (1 + slippage_rate)
            amount = min(decision.maximum_dollar_amount or 0, self.cash)
            if amount <= 0:
                raise ShadowPortfolioError("no cash allocated to BUY")
            quantity = amount / fill_price
            item = self.positions.setdefault(
                decision.symbol, {"quantity": 0.0, "average_cost": 0.0}
            )
            old_quantity = float(item["quantity"])
            new_quantity = old_quantity + quantity
            item["average_cost"] = (
                float(item["average_cost"]) * old_quantity + fill_price * quantity
            ) / new_quantity
            item["quantity"] = new_quantity
            self.cash -= fill_price * quantity
            spread_cost = max(0.0, base_price - midpoint) * quantity
            slippage_cost = max(0.0, fill_price - base_price) * quantity
        else:
            item = self.positions.get(decision.symbol)
            if not item:
                raise ShadowPortfolioError("cannot SELL a missing shadow position")
            quantity = min(
                decision.share_quantity or float(item["quantity"]),
                float(item["quantity"]),
            )
            if quantity <= 0:
                raise ShadowPortfolioError("SELL quantity must be positive")
            base_price = quote.bid
            fill_price = base_price * (1 - slippage_rate)
            realized_pnl = (fill_price - float(item["average_cost"])) * quantity
            self.cash += fill_price * quantity
            item["quantity"] = float(item["quantity"]) - quantity
            if item["quantity"] <= 1e-12:
                del self.positions[decision.symbol]
            spread_cost = max(0.0, midpoint - base_price) * quantity
            slippage_cost = max(0.0, base_price - fill_price) * quantity

        fill = ShadowFill(
            fill_id=str(uuid.uuid4()),
            decision_id=decision.idempotency_key,
            action=decision.action,
            symbol=decision.symbol,
            quantity=quantity,
            price=fill_price,
            gross_value=fill_price * quantity,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            timestamp=datetime.now(timezone.utc),
            realized_pnl=realized_pnl,
        )
        self.fills.append(
            {
                "fill_id": fill.fill_id,
                "decision_id": fill.decision_id,
                "action": fill.action.value,
                "symbol": fill.symbol,
                "quantity": fill.quantity,
                "price": fill.price,
                "gross_value": fill.gross_value,
                "spread_cost": fill.spread_cost,
                "slippage_cost": fill.slippage_cost,
                "realized_pnl": fill.realized_pnl,
                "timestamp": fill.timestamp.isoformat().replace("+00:00", "Z"),
            }
        )
        self._save()
        return fill
