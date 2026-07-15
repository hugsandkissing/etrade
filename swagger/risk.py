"""Deterministic risk checks that decision providers cannot override."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Settings
from .models import Action, AccountState, Decision, HealthState, Quote, RiskVerdict


@dataclass(frozen=True)
class RiskContext:
    account: AccountState
    quote: Quote | None
    health: HealthState
    ledger_writable: bool
    unresolved_order: bool = False
    duplicate_idempotency_key: bool = False


class RiskKernel:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, decision: Decision, context: RiskContext) -> RiskVerdict:
        reasons: list[str] = []
        account = context.account
        target_map: dict[str, float] = {}
        target_delta: float | None = None

        if decision.action is not Action.HOLD and not decision.target_allocations:
            reasons.append("non-HOLD decision requires complete target allocations")
        if decision.target_allocations:
            target_map = {
                item.symbol.upper(): item.weight for item in decision.target_allocations
            }
            if len(target_map) != len(decision.target_allocations):
                reasons.append("target allocation contains duplicate symbols")
            if not decision.target_is_complete:
                reasons.append("portfolio target is not marked complete")
            if any(weight < 0 or weight > 1 for weight in target_map.values()):
                reasons.append("target weights must be between zero and one")
            if sum(target_map.values()) > 1.0 + 1e-9:
                reasons.append("target weights exceed 100%")
            held_symbols = {position.symbol.upper() for position in account.positions}
            missing = held_symbols - set(target_map)
            if missing:
                reasons.append("complete target omits an existing position")
            if (
                sum(weight > 1e-9 for weight in target_map.values())
                > self.settings.max_positions
            ):
                reasons.append("target exceeds maximum open positions")
            if (
                account.value * sum(target_map.values())
                > self.settings.max_capital + 0.01
            ):
                reasons.append("target invested value exceeds maximum capital")
            symbol_target = target_map.get(decision.symbol.upper())
            if symbol_target is None:
                reasons.append("target omits the decision symbol")
            elif context.quote is not None:
                position = next(
                    (
                        item
                        for item in account.positions
                        if item.symbol.upper() == decision.symbol.upper()
                    ),
                    None,
                )
                current_value = (
                    position.quantity * context.quote.midpoint if position else 0.0
                )
                target_delta = account.value * symbol_target - current_value

        if self.settings.mode != "shadow":
            reasons.append("live mode is not implemented")
        if context.health is not HealthState.HEALTHY:
            reasons.append(f"engine health is {context.health.value}")
        if not context.ledger_writable:
            reasons.append("audit ledger is not writable")
        if context.unresolved_order:
            reasons.append("an unresolved order already exists")
        if context.duplicate_idempotency_key:
            reasons.append("duplicate idempotency key")
        if (
            decision.instrument_is_leveraged
            or decision.symbol in self.settings.banned_symbols
        ):
            reasons.append("leveraged or inverse instruments are forbidden")
        if decision.action in {Action.BUY, Action.ROTATE}:
            if account.value <= self.settings.account_floor:
                reasons.append("account floor reached")
            if account.value >= self.settings.account_goal:
                reasons.append("account goal reached")
            if account.daily_pnl_pct <= -self.settings.daily_loss_limit_pct:
                reasons.append("daily loss limit reached")
            if decision.target_allocations:
                if target_delta is not None and target_delta <= 0.01:
                    reasons.append("BUY target has no positive allocation drift")
                if (
                    target_delta is not None
                    and target_delta > account.buying_power + 0.01
                ):
                    reasons.append("target drift exceeds buying power")
                if (
                    target_delta is not None
                    and target_delta > self.settings.max_capital + 0.01
                ):
                    reasons.append("target drift exceeds maximum capital")
            else:
                if len(account.positions) >= self.settings.max_positions:
                    reasons.append("maximum open positions reached")
                amount = decision.maximum_dollar_amount or 0
                if amount <= 0:
                    reasons.append("buy proposal has no positive maximum amount")
                if amount > account.buying_power:
                    reasons.append("proposal exceeds buying power")
                if amount > self.settings.max_capital:
                    reasons.append("proposal exceeds maximum capital")

        if decision.action is Action.SELL and decision.target_allocations:
            if target_delta is not None and target_delta >= -0.01:
                reasons.append("SELL target has no negative allocation drift")

        if decision.action is not Action.HOLD:
            if context.quote is None:
                reasons.append("fresh execution-side quote is missing")
            else:
                age = (
                    datetime.now(timezone.utc) - context.quote.timestamp
                ).total_seconds()
                if age > self.settings.stale_seconds:
                    reasons.append("execution-side quote is stale")
                if context.quote.spread_pct > self.settings.max_spread_pct:
                    reasons.append("bid/ask spread exceeds configured maximum")

        return RiskVerdict(approved=not reasons, reasons=tuple(reasons))
