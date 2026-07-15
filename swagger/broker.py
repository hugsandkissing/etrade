"""Broker boundary. No order-capable implementation exists in v0.1."""

from __future__ import annotations

from typing import Protocol

from dataclasses import dataclass
import math

from .models import (
    AccountState,
    Action,
    Decision,
    ExecutionOrder,
    Quote,
    TargetAllocation,
)


class BrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerCapabilities:
    supports_fractional_shares: bool
    quantity_increment: float = 1.0


def target_for(decision: Decision, symbol: str) -> TargetAllocation | None:
    return next(
        (item for item in decision.target_allocations if item.symbol == symbol), None
    )


def plan_allocation_order(
    *,
    target: TargetAllocation,
    account: AccountState,
    quote: Quote,
    capabilities: BrokerCapabilities,
) -> ExecutionOrder | None:
    """Translate target-weight drift into an adapter-specific executable order."""
    if not 0 <= target.weight <= 1:
        raise ValueError("target allocation weight must be between 0 and 1")
    position = next((p for p in account.positions if p.symbol == target.symbol), None)
    current_quantity = position.quantity if position else 0.0
    current_value = current_quantity * quote.midpoint
    target_value = account.value * target.weight
    delta = target_value - current_value
    if abs(delta) < 0.01:
        return None
    action = Action.BUY if delta > 0 else Action.SELL
    price = quote.ask if action is Action.BUY else quote.bid
    raw_quantity = abs(delta) / price
    if capabilities.supports_fractional_shares:
        quantity = raw_quantity
    else:
        increment = capabilities.quantity_increment
        quantity = math.floor(raw_quantity / increment) * increment
    if action is Action.SELL:
        quantity = min(quantity, current_quantity)
    if quantity <= 0:
        return None
    estimated_value = quantity * price
    residual = max(0.0, abs(delta) - estimated_value)
    return ExecutionOrder(
        symbol=target.symbol,
        action=action,
        quantity=quantity,
        estimated_price=price,
        estimated_value=estimated_value,
        target_weight=target.weight,
        residual_cash=residual,
    )


class Broker(Protocol):
    async def account_state(self) -> AccountState: ...

    async def quote(self, symbol: str) -> Quote: ...

    async def has_open_order(self, symbol: str) -> bool: ...

    async def place_order(self, decision: Decision) -> None: ...


class FailClosedMockBroker:
    """Explicitly refuses every broker action, including order placement."""

    async def account_state(self) -> AccountState:
        raise BrokerUnavailable("real broker state is unavailable in mock mode")

    async def quote(self, symbol: str) -> Quote:
        raise BrokerUnavailable(f"broker quote unavailable for {symbol} in mock mode")

    async def has_open_order(self, symbol: str) -> bool:
        return False

    async def place_order(self, decision: Decision) -> None:
        raise BrokerUnavailable("order placement is intentionally unimplemented")
