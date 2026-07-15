"""Structured shadow decision providers. They never call brokers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .config import Settings
from .models import Action, Decision, Position, Quote, Signal, TargetAllocation
from .signals import distinct_families, signal_names


@dataclass(frozen=True)
class DecisionContext:
    symbol: str
    timestamp: datetime
    signals: tuple[Signal, ...]
    quote: Quote | None
    position: Position | None
    buying_power: float
    account_value: float
    current_allocations: tuple[TargetAllocation, ...] = ()


class DecisionProvider(Protocol):
    async def propose(self, context: DecisionContext) -> Decision: ...


def _key(context: DecisionContext, action: Action) -> str:
    bucket = int(context.timestamp.timestamp()) // 60
    raw = f"{action.value}:{context.symbol}:{bucket}:{','.join(signal_names(context.signals))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class RuleBasedDecisionProvider:
    """Conservative default requiring confirmation from distinct signal families."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_proposal: dict[str, datetime] = {}

    async def propose(self, context: DecisionContext) -> Decision:
        now = context.timestamp.astimezone(timezone.utc)
        recent = self._last_proposal.get(context.symbol)
        if (
            recent
            and (now - recent).total_seconds() < self.settings.proposal_cooldown_seconds
        ):
            return self._hold(context, "Proposal cooldown is active")

        bullish = distinct_families(context.signals, "bullish")
        bearish = distinct_families(context.signals, "bearish")
        adverse = any(
            signal.name in {"material_adverse_news", "confirmed_stop"}
            for signal in context.signals
        )

        if context.position and (adverse or len(bearish) >= 2):
            decision = self._decision(
                context,
                Action.SELL,
                min(0.95, 0.60 + 0.10 * len(bearish)),
                "Independent bearish evidence threatens the shadow position",
                None,
                context.position.quantity,
                "Immediate risk exit",
                "Bearish signals clear and price reclaims prior support",
                None,
            )
            self._last_proposal[context.symbol] = now
            return decision

        if not context.position and len(bullish) >= 2:
            amount = min(
                context.buying_power,
                self.settings.max_capital / self.settings.max_positions,
            )
            decision = self._decision(
                context,
                Action.BUY,
                min(0.90, 0.55 + 0.10 * len(bullish)),
                "Bullish evidence is confirmed by distinct signal families",
                amount,
                None,
                "Intraday to five sessions",
                "Relative strength fails or price loses the triggering level",
                -7.0,
            )
            self._last_proposal[context.symbol] = now
            return decision

        return self._hold(context, "No sufficiently independent confirmation")

    def _hold(self, context: DecisionContext, rationale: str) -> Decision:
        return self._decision(
            context, Action.HOLD, 1.0, rationale, None, None, "N/A", "N/A", None
        )

    def _decision(
        self,
        context: DecisionContext,
        action: Action,
        confidence: float,
        rationale: str,
        maximum_dollar_amount: float | None,
        share_quantity: float | None,
        expected_holding_period: str,
        invalidation_condition: str,
        suggested_protective_exit_pct: float | None,
    ) -> Decision:
        targets: tuple[TargetAllocation, ...] = ()
        if action is not Action.HOLD:
            target_map = {
                item.symbol.upper(): item.weight for item in context.current_allocations
            }
            if action is Action.SELL:
                target_map[context.symbol.upper()] = 0.0
            elif action in {Action.BUY, Action.ROTATE}:
                target_map[context.symbol.upper()] = min(
                    1.0 / self.settings.max_positions,
                    (maximum_dollar_amount or 0) / context.account_value
                    if context.account_value > 0
                    else 0.0,
                )
            targets = tuple(
                TargetAllocation(symbol, weight)
                for symbol, weight in sorted(target_map.items())
            )

        return Decision(
            action=action,
            symbol=context.symbol,
            timestamp=context.timestamp,
            confidence=confidence,
            triggering_signals=signal_names(context.signals),
            rationale=rationale,
            maximum_dollar_amount=maximum_dollar_amount,
            share_quantity=share_quantity,
            expected_holding_period=expected_holding_period,
            invalidation_condition=invalidation_condition,
            suggested_protective_exit_pct=suggested_protective_exit_pct,
            idempotency_key=_key(context, action),
            target_allocations=targets,
            target_is_complete=action is not Action.HOLD,
        )


class OpenAIDecisionProvider:
    """Optional seam for a future structured-output provider; fail closed today."""

    async def propose(self, context: DecisionContext) -> Decision:
        return RuleBasedDecisionProvider(Settings())._hold(
            context, "OpenAI decision provider is not configured; failed closed"
        )
