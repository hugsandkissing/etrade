"""Durable hypothetical portfolio accounting, separate from any real broker."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker import BrokerCapabilities, plan_allocation_order, target_for
from .models import (
    ALLOCATION_WEIGHT_EPSILON,
    Action,
    AccountState,
    AllocationLine,
    AllocationSnapshot,
    Decision,
    Position,
    Quote,
    ShadowFill,
    TargetAllocation,
)


class ShadowPortfolioError(RuntimeError):
    pass


class ShadowPortfolio:
    def __init__(
        self,
        path: Path,
        starting_cash: float,
        slippage_bps: float,
        max_order_notional: float | None = None,
        max_capital: float | None = None,
    ):
        self.path = path
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.max_order_notional = max_order_notional
        self.max_capital = max_capital
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

    def realized_allocations(
        self, quotes: dict[str, Quote]
    ) -> tuple[TargetAllocation, ...]:
        """Return every held symbol's current portfolio weight.

        These weights seed the next complete target so an action on one symbol
        cannot silently imply liquidating every other holding.
        """
        account = self.account_state(quotes)
        allocations: list[TargetAllocation] = []
        for symbol in sorted(self.positions):
            position = self.position(symbol)
            if position is None:
                continue
            quote = quotes.get(symbol)
            value = position.quantity * (
                quote.midpoint if quote else position.average_cost
            )
            allocations.append(
                TargetAllocation(
                    symbol=symbol,
                    weight=value / account.value if account.value else 0.0,
                )
            )
        return tuple(allocations)

    def allocation_snapshot(
        self,
        quotes: dict[str, Quote],
        targets: tuple[TargetAllocation, ...],
        execution_residual_cash: float = 0.0,
    ) -> AllocationSnapshot:
        account = self.account_state(quotes)
        target_map = {item.symbol.upper(): item.weight for item in targets}
        if len(target_map) != len(targets):
            raise ShadowPortfolioError("target allocation contains duplicate symbols")
        if any(weight < 0 or weight > 1 for weight in target_map.values()):
            raise ShadowPortfolioError("target weights must be between zero and one")
        if sum(target_map.values()) > 1.0 + ALLOCATION_WEIGHT_EPSILON:
            raise ShadowPortfolioError("target weights exceed 100%")
        missing = set(self.positions) - set(target_map)
        if missing:
            raise ShadowPortfolioError(
                "complete target omits held symbols: " + ", ".join(sorted(missing))
            )
        symbols = sorted(set(target_map) | set(self.positions))
        lines: list[AllocationLine] = []
        for symbol in symbols:
            position = self.position(symbol)
            quote = quotes.get(symbol)
            realized_value = 0.0
            if position:
                realized_value = position.quantity * (
                    quote.midpoint if quote else position.average_cost
                )
            realized_weight = realized_value / account.value if account.value else 0.0
            target_weight = target_map.get(symbol, 0.0)
            lines.append(
                AllocationLine(
                    symbol=symbol,
                    target_weight=target_weight,
                    realized_weight=realized_weight,
                    drift_weight=target_weight - realized_weight,
                    target_value=account.value * target_weight,
                    realized_value=realized_value,
                )
            )
        return AllocationSnapshot(
            portfolio_value=account.value,
            cash=self.cash,
            target_cash_weight=max(0.0, 1.0 - sum(target_map.values())),
            realized_cash_weight=self.cash / account.value if account.value else 0.0,
            target_cash_value=account.value * max(0.0, 1.0 - sum(target_map.values())),
            realized_cash_value=self.cash,
            execution_residual_cash=execution_residual_cash,
            lines=tuple(lines),
        )

    def execute(self, decision: Decision, quote: Quote) -> ShadowFill:
        if decision.action not in {Action.BUY, Action.SELL}:
            raise ShadowPortfolioError(f"cannot shadow-fill {decision.action.value}")
        slippage_rate = self.slippage_bps / 10_000
        midpoint = quote.midpoint
        realized_pnl = 0.0
        target = target_for(decision, decision.symbol)

        if target is not None:
            funded_room = (
                max(
                    0.0,
                    self.max_capital
                    - sum(
                        item.quantity * item.average_cost
                        for item in self.account_state({quote.symbol: quote}).positions
                    ),
                )
                if self.max_capital is not None
                else None
            )
            order_limit = self.max_order_notional
            if decision.action is Action.SELL:
                order_limit = None
            if funded_room is not None:
                if decision.action is not Action.SELL:
                    order_limit = (
                        funded_room
                        if order_limit is None
                        else min(order_limit, funded_room)
                    )
            planned = plan_allocation_order(
                target=target,
                account=self.account_state({quote.symbol: quote}),
                quote=quote,
                capabilities=BrokerCapabilities(supports_fractional_shares=True),
                max_notional=order_limit,
            )
            if planned is None:
                raise ShadowPortfolioError("target allocation is already satisfied")
            if planned.action is not decision.action:
                raise ShadowPortfolioError(
                    "decision action conflicts with target drift"
                )
        else:
            planned = None

        if decision.action is Action.BUY:
            base_price = quote.ask
            fill_price = base_price * (1 + slippage_rate)
            amount = min(
                planned.estimated_value
                if planned
                else decision.maximum_dollar_amount or 0,
                self.cash,
            )
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
                planned.quantity
                if planned
                else decision.share_quantity or float(item["quantity"]),
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

        post_account = self.account_state({quote.symbol: quote})
        post_position = self.position(decision.symbol)
        post_value = (post_position.quantity * quote.midpoint) if post_position else 0.0
        execution_residual = planned.residual_cash if planned else 0.0
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
            target_weight=target.weight if target else None,
            realized_weight_after=(
                post_value / post_account.value if post_account.value else 0.0
            ),
            execution_residual_cash_after=execution_residual,
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
                "target_weight": fill.target_weight,
                "realized_weight_after": fill.realized_weight_after,
                "execution_residual_cash_after": fill.execution_residual_cash_after,
                "timestamp": fill.timestamp.isoformat().replace("+00:00", "Z"),
            }
        )
        self._save()
        return fill
