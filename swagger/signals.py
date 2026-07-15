"""Signal names and family helpers used by decision confirmation rules."""

from __future__ import annotations

from collections.abc import Iterable

from .models import Signal

BULLISH_DIRECTIONS = {"bullish"}
BEARISH_DIRECTIONS = {"bearish"}


def distinct_families(
    signals: Iterable[Signal], direction: str | None = None
) -> set[str]:
    return {
        signal.family
        for signal in signals
        if direction is None or signal.direction == direction
    }


def signal_names(signals: Iterable[Signal]) -> tuple[str, ...]:
    return tuple(sorted({signal.name for signal in signals}))
