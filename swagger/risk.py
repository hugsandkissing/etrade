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
            if len(account.positions) >= self.settings.max_positions:
                reasons.append("maximum open positions reached")
            amount = decision.maximum_dollar_amount or 0
            if amount <= 0:
                reasons.append("buy proposal has no positive maximum amount")
            if amount > account.buying_power:
                reasons.append("proposal exceeds buying power")
            if amount > self.settings.max_capital:
                reasons.append("proposal exceeds maximum capital")

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
