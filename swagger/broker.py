"""Broker boundary. No order-capable implementation exists in v0.1."""

from __future__ import annotations

from typing import Protocol

from .models import AccountState, Decision, Quote


class BrokerUnavailable(RuntimeError):
    pass


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
