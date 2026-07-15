"""Turn raw market events into sparse, meaningful signals."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings
from .models import EventType, MarketEvent, Quote, Signal


@dataclass
class SymbolState:
    session_high: float | None = None
    session_low: float | None = None
    last_bar_close: float | None = None
    quote: Quote | None = None
    bar_volumes: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    position_average_cost: float | None = None
    crossed_position_thresholds: set[float] = field(default_factory=set)


class SignalAggregator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.states: dict[str, SymbolState] = defaultdict(SymbolState)
        self.last_event_at: datetime | None = None

    def set_position_average_cost(self, symbol: str, average_cost: float) -> None:
        self.states[symbol.upper()].position_average_cost = average_cost

    def current_quote(self, symbol: str) -> Quote | None:
        return self.states[symbol.upper()].quote

    def process(self, event: MarketEvent) -> tuple[Signal, ...]:
        self.last_event_at = max(self.last_event_at or event.timestamp, event.timestamp)
        state = self.states[event.symbol]
        signals: list[Signal] = []

        if event.event_type is EventType.QUOTE and event.bid and event.ask:
            state.quote = Quote(
                event.symbol, event.bid, event.ask, event.timestamp, event.source
            )
            if state.quote.spread_pct > self.settings.max_spread_pct:
                signals.append(
                    self._signal(
                        event,
                        "wide_spread",
                        "liquidity",
                        "neutral",
                        1.0,
                        spread_pct=state.quote.spread_pct,
                    )
                )

        if event.event_type is EventType.BAR and event.price is not None:
            prior_high, prior_low, prior_close = (
                state.session_high,
                state.session_low,
                state.last_bar_close,
            )
            if prior_high is not None and event.price > prior_high:
                signals.append(
                    self._signal(
                        event,
                        "session_high_cross",
                        "price",
                        "bullish",
                        0.65,
                        prior_high=prior_high,
                        price=event.price,
                    )
                )
            if prior_low is not None and event.price < prior_low:
                signals.append(
                    self._signal(
                        event,
                        "session_low_cross",
                        "price",
                        "bearish",
                        0.65,
                        prior_low=prior_low,
                        price=event.price,
                    )
                )
            state.session_high = max(prior_high or event.price, event.price)
            state.session_low = min(prior_low or event.price, event.price)

            if event.volume is not None:
                baseline = (
                    sum(state.bar_volumes) / len(state.bar_volumes)
                    if state.bar_volumes
                    else None
                )
                if (
                    baseline
                    and event.volume >= baseline * 2
                    and event.volume >= self.settings.min_five_minute_volume
                ):
                    direction = (
                        "bullish"
                        if prior_close is None or event.price >= prior_close
                        else "bearish"
                    )
                    signals.append(
                        self._signal(
                            event,
                            "relative_volume",
                            "volume",
                            direction,
                            0.7,
                            multiple=event.volume / baseline,
                            volume=event.volume,
                        )
                    )
                state.bar_volumes.append(event.volume)

            if prior_close and event.symbol in {"SPY", "QQQ", "XLE"}:
                move_pct = (event.price - prior_close) / prior_close * 100
                if abs(move_pct) >= 0.5:
                    signals.append(
                        self._signal(
                            event,
                            "benchmark_abrupt_move",
                            "market",
                            "bullish" if move_pct > 0 else "bearish",
                            min(1.0, abs(move_pct) / 2),
                            move_pct=move_pct,
                        )
                    )
            state.last_bar_close = event.price

        if event.event_type is EventType.NEWS and event.headline:
            signals.append(
                self._signal(
                    event,
                    "breaking_news",
                    "news",
                    "neutral",
                    0.5,
                    headline=event.headline,
                )
            )

        price = event.price or (state.quote.midpoint if state.quote else None)
        if price and state.position_average_cost:
            move_pct = (
                (price - state.position_average_cost)
                / state.position_average_cost
                * 100
            )
            for threshold in (3.0, 5.0, 7.0, 10.0):
                signed = threshold if move_pct > 0 else -threshold
                if (
                    abs(move_pct) >= threshold
                    and signed not in state.crossed_position_thresholds
                ):
                    state.crossed_position_thresholds.add(signed)
                    signals.append(
                        self._signal(
                            event,
                            "position_move",
                            "position",
                            "bullish" if move_pct > 0 else "bearish",
                            min(1.0, abs(move_pct) / 10),
                            move_pct=move_pct,
                            threshold=signed,
                        )
                    )
        return tuple(signals)

    def stale(self, now: datetime | None = None) -> bool:
        if self.last_event_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return (now - self.last_event_at).total_seconds() > self.settings.stale_seconds

    @staticmethod
    def _signal(
        event: MarketEvent,
        name: str,
        family: str,
        direction: str,
        strength: float,
        **details,
    ) -> Signal:
        return Signal(
            name, family, event.symbol, event.timestamp, direction, strength, details
        )
