"""Typed domain models shared across the Swagger Engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    TRADE = "trade"
    QUOTE = "quote"
    BAR = "bar"
    NEWS = "news"
    STATUS = "status"


class Action(str, Enum):
    HOLD = "HOLD"
    BUY = "BUY"
    SELL = "SELL"
    ROTATE = "ROTATE"


class HealthState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    HALTED = "halted"


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    event_type: EventType
    symbol: str
    timestamp: datetime
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    headline: str | None = None
    source: str = "alpaca"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Signal:
    name: str
    family: str
    symbol: str
    timestamp: datetime
    direction: str
    strength: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime
    source: str

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return 0 if self.midpoint <= 0 else (self.ask - self.bid) / self.midpoint * 100


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    average_cost: float


@dataclass(frozen=True)
class AccountState:
    value: float
    buying_power: float
    positions: tuple[Position, ...] = ()
    daily_pnl_pct: float = 0.0
    verified_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class Decision:
    action: Action
    symbol: str
    timestamp: datetime
    confidence: float
    triggering_signals: tuple[str, ...]
    rationale: str
    maximum_dollar_amount: float | None
    share_quantity: float | None
    expected_holding_period: str
    invalidation_condition: str
    suggested_protective_exit_pct: float | None
    idempotency_key: str
    rotate_from: str | None = None
    instrument_is_leveraged: bool = False


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ShadowFill:
    fill_id: str
    decision_id: str
    action: Action
    symbol: str
    quantity: float
    price: float
    gross_value: float
    spread_cost: float
    slippage_cost: float
    timestamp: datetime
    realized_pnl: float = 0.0


def jsonable(value: Any) -> Any:
    """Convert domain values to JSON-safe primitives."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value
