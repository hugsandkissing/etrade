"""Replaceable real-time market stream with a resilient Alpaca adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .models import EventType, HealthState, MarketEvent

try:
    import websockets
except ImportError:  # pragma: no cover - covered by the startup dependency check
    websockets = None

StatusCallback = Callable[[HealthState, str], Awaitable[None]]


class MarketDataStream(ABC):
    @abstractmethod
    def events(self) -> AsyncIterator[MarketEvent]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


def _timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _event_id(message: dict[str, Any]) -> str:
    stable = json.dumps(message, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode()).hexdigest()


class AlpacaWebSocketStream(MarketDataStream):
    def __init__(self, settings: Settings, on_status: StatusCallback | None = None):
        self.settings = settings
        self.on_status = on_status
        self._closed = False
        self._seen: set[str] = set()

    async def _status(self, state: HealthState, detail: str) -> None:
        if self.on_status:
            await self.on_status(state, detail)

    async def close(self) -> None:
        self._closed = True

    def _connect(self):
        if websockets is None:
            raise RuntimeError(
                "websockets is not installed; run pip install -r requirements.txt"
            )
        return websockets.connect(
            self.settings.alpaca_stream_url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_queue=4096,
        )

    async def _authenticate_and_subscribe(self, websocket) -> None:
        await websocket.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.settings.alpaca_api_key,
                    "secret": self.settings.alpaca_api_secret,
                }
            )
        )
        auth = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        if not any(
            item.get("T") == "success" and item.get("msg") == "authenticated"
            for item in auth
        ):
            raise RuntimeError(f"Alpaca authentication failed: {auth}")
        symbols = list(self.settings.symbols)
        await websocket.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "trades": symbols,
                    "quotes": symbols,
                    "bars": symbols,
                }
            )
        )

    async def events(self) -> AsyncIterator[MarketEvent]:
        attempts = 0
        while not self._closed:
            try:
                await self._status(HealthState.STARTING, "connecting to Alpaca")
                async with self._connect() as websocket:
                    await self._authenticate_and_subscribe(websocket)
                    attempts = 0
                    await self._status(
                        HealthState.HEALTHY, "Alpaca stream authenticated"
                    )
                    async for raw in websocket:
                        for message in json.loads(raw):
                            event = self._parse(message)
                            if event is None or event.event_id in self._seen:
                                continue
                            self._seen.add(event.event_id)
                            if len(self._seen) > 50_000:
                                self._seen.clear()
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts += 1
                if attempts >= self.settings.reconnect_max_attempts:
                    await self._status(
                        HealthState.HALTED, f"stream reconnect exhausted: {exc}"
                    )
                    raise RuntimeError("Alpaca reconnect attempts exhausted") from exc
                await self._status(HealthState.DEGRADED, f"stream disconnected: {exc}")
                base = min(2 ** (attempts - 1), self.settings.reconnect_max_seconds)
                await asyncio.sleep(base + random.uniform(0, min(1.0, base / 4)))

    def _parse(self, item: dict[str, Any]) -> MarketEvent | None:
        kind = item.get("T")
        mapping = {
            "t": EventType.TRADE,
            "q": EventType.QUOTE,
            "b": EventType.BAR,
            "n": EventType.NEWS,
        }
        event_type = mapping.get(kind)
        if event_type is None:
            return None
        symbol = str(item.get("S") or (item.get("symbols") or [""])[0]).upper()
        if not symbol:
            return None
        return MarketEvent(
            event_id=_event_id(item),
            event_type=event_type,
            symbol=symbol,
            timestamp=_timestamp(item.get("t") or item.get("created_at")),
            price=item.get("p")
            if kind == "t"
            else item.get("c")
            if kind == "b"
            else None,
            bid=item.get("bp"),
            ask=item.get("ap"),
            volume=item.get("v"),
            headline=item.get("headline"),
            raw=item,
        )
