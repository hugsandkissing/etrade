"""Always-on shadow/preview/live engine with fail-closed broker execution."""

from __future__ import annotations

import argparse
import asyncio
import signal
import time
import uuid
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .bar_aggregator import SignalAggregator
from .broker import (
    BrokerCapabilities,
    FailClosedMockBroker,
    plan_allocation_order,
    target_for,
)
from .config import ConfigurationError, Settings
from .decision_engine import DecisionContext, RuleBasedDecisionProvider
from .health import HealthServer, HealthTracker
from .ledger import AuditLedger
from .market_hours import is_regular_session
from .market_stream import AlpacaWebSocketStream
from .models import (
    Action,
    AccountState,
    BrokerOrderResult,
    EventType,
    HealthState,
    Position,
    Quote,
    Signal,
    TargetAllocation,
)
from .notifier import Notifier, configure_logging, emit
from .risk import RiskContext, RiskKernel
from .robinhood_mcp import RobinhoodMCPClient, RobinhoodMCPError
from .shadow import ShadowPortfolio, ShadowPortfolioError


class ShadowEngine:
    def __init__(self, settings: Settings, health: HealthTracker | None = None):
        self.settings = settings
        self.settings.validate(require_market_data=True)
        self.logger = configure_logging()
        self.notifier = Notifier(self.logger, settings.notifications_enabled)
        self.health = health or HealthTracker()
        self.ledger = AuditLedger(
            settings.ledger_path,
            max_bytes=settings.ledger_max_bytes,
            archive_dir=settings.ledger_archive_dir,
        )
        self.portfolio = ShadowPortfolio(
            settings.state_path,
            settings.starting_cash,
            settings.shadow_slippage_bps,
            settings.max_order_notional,
            settings.max_capital,
        )
        self.aggregator = SignalAggregator(settings)
        self.provider = RuleBasedDecisionProvider(settings)
        self.risk = RiskKernel(settings)
        self.broker = FailClosedMockBroker()
        self.robinhood: RobinhoodMCPClient | None = None
        if settings.broker_mode.startswith("robinhood_"):
            access_mode = {
                "robinhood_readonly": "readonly",
                "robinhood_preview": "preview",
                "robinhood_live": "live",
            }[settings.broker_mode]
            self.robinhood = RobinhoodMCPClient(
                account_number=settings.robinhood_account_number,
                server_url=settings.robinhood_mcp_url,
                callback_port=settings.robinhood_oauth_callback_port,
                access_mode=access_mode,
            )
        self._broker_snapshot = None
        self.stream = AlpacaWebSocketStream(settings, self._stream_status)
        self.signal_buffer: dict[str, deque[Signal]] = defaultdict(
            lambda: deque(maxlen=32)
        )
        self.seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._last_quote_persisted: dict[str, datetime] = {}
        self.stop_event = asyncio.Event()
        for position in self.portfolio.account_state().positions:
            self.aggregator.set_position_average_cost(
                position.symbol, position.average_cost
            )

    async def _stream_status(self, state: HealthState, detail: str) -> None:
        self.health.set(state, detail)
        self.ledger.append(
            "health_transition", {"state": state.value, "detail": detail}
        )
        emit(self.logger, "health_transition", state=state.value, detail=detail)
        if state is HealthState.HALTED:
            self.notifier.send("Swagger Engine halted", detail)

    def _quotes(self) -> dict[str, Quote]:
        return {
            symbol: quote
            for symbol in self.settings.symbols
            if (quote := self.aggregator.current_quote(symbol)) is not None
        }

    @staticmethod
    def _snapshot_account(snapshot) -> AccountState:
        return AccountState(
            value=snapshot.portfolio_value,
            buying_power=snapshot.buying_power,
            positions=snapshot.positions,
            verified_at=snapshot.verified_at,
        )

    @staticmethod
    def _position_from_account(account: AccountState, symbol: str) -> Position | None:
        return next((item for item in account.positions if item.symbol == symbol), None)

    @staticmethod
    def _realized_allocations(
        account: AccountState, quotes: dict[str, Quote]
    ) -> tuple[TargetAllocation, ...]:
        allocations: list[TargetAllocation] = []
        for position in sorted(account.positions, key=lambda item: item.symbol):
            quote = quotes.get(position.symbol)
            value = position.quantity * (
                quote.midpoint if quote else position.average_cost
            )
            allocations.append(
                TargetAllocation(
                    position.symbol,
                    value / account.value if account.value else 0.0,
                )
            )
        return tuple(allocations)

    def _remember_event(self, event_id: str) -> bool:
        if event_id in self.seen_event_ids:
            return False
        self.seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        while len(self._seen_event_order) > 50_000:
            expired = self._seen_event_order.popleft()
            self.seen_event_ids.discard(expired)
        return True

    def _persist_market_event(self, event) -> bool:
        if event.event_type in {EventType.BAR, EventType.NEWS, EventType.STATUS}:
            return True
        if event.event_type is EventType.TRADE:
            return False
        if event.event_type is EventType.QUOTE:
            previous = self._last_quote_persisted.get(event.symbol)
            if (
                previous is not None
                and (event.timestamp - previous).total_seconds()
                < self.settings.ledger_quote_sample_seconds
            ):
                return False
            self._last_quote_persisted[event.symbol] = event.timestamp
            return True
        return False

    async def _stale_watchdog(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(max(1, self.settings.stale_seconds // 2))
            if self.settings.kill_switch_path.exists():
                await self._stream_status(
                    HealthState.HALTED, "kill switch file is present"
                )
                self.stop_event.set()
                await self.stream.close()
                return
            now = datetime.now(timezone.utc)
            if (
                self.health.state is HealthState.HEALTHY
                and is_regular_session(now)
                and self.aggregator.stale(now)
            ):
                await self._stream_status(HealthState.DEGRADED, "market data is stale")

    def _broker_discrepancies(self, snapshot) -> list[dict[str, object]]:
        shadow = {
            position.symbol: position
            for position in self.portfolio.account_state(self._quotes()).positions
        }
        real = {position.symbol: position for position in snapshot.positions}
        discrepancies: list[dict[str, object]] = []
        for symbol in sorted(set(shadow) | set(real)):
            shadow_position = shadow.get(symbol)
            real_position = real.get(symbol)
            shadow_quantity = shadow_position.quantity if shadow_position else 0.0
            real_quantity = real_position.quantity if real_position else 0.0
            if abs(shadow_quantity - real_quantity) > 1e-8:
                discrepancies.append(
                    {
                        "symbol": symbol,
                        "shadow_quantity": shadow_quantity,
                        "real_quantity": real_quantity,
                    }
                )
        return discrepancies

    async def _broker_reconciler(self) -> None:
        if self.robinhood is None:
            return
        try:
            while not self.stop_event.is_set():
                snapshot = await self.robinhood.snapshot(self.settings.symbols)
                self._broker_snapshot = snapshot
                self.ledger.append(
                    "robinhood_snapshot", snapshot.audit_payload()
                )
                if self.settings.mode == "shadow":
                    discrepancies = self._broker_discrepancies(snapshot)
                    if discrepancies:
                        self.ledger.append(
                            "broker_reconciliation_discrepancy",
                            {"items": discrepancies},
                        )
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.broker_reconcile_seconds,
                    )
                except TimeoutError:
                    continue
        except (RobinhoodMCPError, OSError, ValueError) as exc:
            await self._stream_status(
                HealthState.HALTED,
                f"Robinhood reconciliation failed: {exc}",
            )
            self.stop_event.set()
            await self.stream.close()

    async def _handle_event(self, event) -> None:
        if not self._remember_event(event.event_id):
            return
        if self.health.state is HealthState.DEGRADED:
            await self._stream_status(HealthState.HEALTHY, "market data resumed")
        if self._persist_market_event(event):
            self.ledger.append("market_event", event)
        signals = self.aggregator.process(event)
        guard_account = (
            self.portfolio.account_state(self._quotes())
            if self.settings.mode == "shadow"
            else (
                self._snapshot_account(self._broker_snapshot)
                if self._broker_snapshot is not None
                else None
            )
        )
        if guard_account is not None and self._position_from_account(
            guard_account, event.symbol
        ):
            guard_name = None
            if guard_account.value <= self.settings.account_floor:
                guard_name = "account_floor_threat"
            elif guard_account.value >= self.settings.account_goal:
                guard_name = "account_goal_reached"
            if guard_name:
                signals.append(
                    Signal(
                        name=guard_name,
                        family="risk",
                        symbol=event.symbol,
                        timestamp=event.timestamp,
                        direction="bearish",
                        strength=1.0,
                        details={"account_value": guard_account.value},
                    )
                )
        if event.event_type is EventType.BAR and self.settings.mode == "shadow":
            account = self.portfolio.account_state(self._quotes())
            self.ledger.append("shadow_snapshot", account)
        if not signals:
            return
        for market_signal in signals:
            self.ledger.append("signal", market_signal)
            self.signal_buffer[event.symbol].append(market_signal)

        if not is_regular_session(event.timestamp):
            self.ledger.append(
                "decision_suppressed",
                {
                    "event_id": event.event_id,
                    "reason": "outside regular US equity session",
                },
            )
            return

        cutoff = event.timestamp - timedelta(minutes=5)
        recent = tuple(
            signal
            for signal in self.signal_buffer[event.symbol]
            if signal.timestamp >= cutoff
        )
        if self.settings.mode == "shadow":
            account = self.portfolio.account_state(self._quotes())
            position = self.portfolio.position(event.symbol)
            allocations = self.portfolio.realized_allocations(self._quotes())
        else:
            if self._broker_snapshot is None:
                self.ledger.append(
                    "decision_suppressed",
                    {
                        "event_id": event.event_id,
                        "reason": "broker state has not been verified",
                    },
                )
                return
            account = self._snapshot_account(self._broker_snapshot)
            position = self._position_from_account(account, event.symbol)
            broker_quotes = {
                quote.symbol: quote for quote in self._broker_snapshot.quotes
            }
            allocations = self._realized_allocations(account, broker_quotes)
        decision = await self.provider.propose(
            DecisionContext(
                symbol=event.symbol,
                timestamp=event.timestamp,
                signals=recent,
                quote=self.aggregator.current_quote(event.symbol),
                position=position,
                buying_power=account.buying_power,
                account_value=account.value,
                invested_cost=sum(
                    item.quantity * item.average_cost for item in account.positions
                ),
                current_allocations=allocations,
            )
        )
        duplicate_key = self.ledger.contains_idempotency_key(decision.idempotency_key)
        self.ledger.append("decision", decision)
        if decision.action is Action.HOLD:
            return

        if self.settings.mode == "shadow":
            quote = self.aggregator.current_quote(event.symbol)
            unresolved_order = False
        else:
            if self.robinhood is None:
                await self._stream_status(
                    HealthState.HALTED, "broker client is unavailable"
                )
                self.stop_event.set()
                return
            try:
                snapshot = await self.robinhood.snapshot(self.settings.symbols)
            except (RobinhoodMCPError, OSError, ValueError) as exc:
                await self._stream_status(
                    HealthState.HALTED,
                    f"pre-order broker verification failed: {exc}",
                )
                self.stop_event.set()
                await self.stream.close()
                return
            self._broker_snapshot = snapshot
            self.ledger.append("pre_order_broker_snapshot", snapshot.audit_payload())
            account = self._snapshot_account(snapshot)
            quote = next(
                (item for item in snapshot.quotes if item.symbol == event.symbol),
                None,
            )
            unresolved_order = event.symbol in snapshot.open_order_symbols
        verdict = self.risk.evaluate(
            decision,
            RiskContext(
                account=account,
                quote=quote,
                health=self.health.state,
                ledger_writable=self.ledger.writable(),
                unresolved_order=unresolved_order,
                duplicate_idempotency_key=duplicate_key,
            ),
        )
        self.ledger.append(
            "risk_verdict",
            {"idempotency_key": decision.idempotency_key, **verdict.__dict__},
        )
        if not verdict.approved or quote is None:
            return
        if self.settings.mode != "shadow":
            await self._handle_broker_decision(decision, account, quote)
            return
        try:
            fill = self.portfolio.execute(decision, quote)
        except ShadowPortfolioError as exc:
            self.ledger.append(
                "shadow_fill_rejected",
                {"idempotency_key": decision.idempotency_key, "reason": str(exc)},
            )
            return
        self.ledger.append("shadow_fill", fill)
        self.ledger.append(
            "allocation_snapshot",
            self.portfolio.allocation_snapshot(
                self._quotes(),
                decision.target_allocations,
                fill.execution_residual_cash_after or 0.0,
            ),
        )
        updated = self.portfolio.position(event.symbol)
        if updated:
            self.aggregator.set_position_average_cost(
                updated.symbol, updated.average_cost
            )
        emit(self.logger, "shadow_fill", fill=fill)

    async def _handle_broker_decision(
        self, decision, account: AccountState, quote: Quote
    ) -> None:
        """Preview or execute one allocation-derived order after fresh verification."""
        if self.robinhood is None or self._broker_snapshot is None:
            await self._stream_status(
                HealthState.HALTED, "broker execution requested without verified state"
            )
            self.stop_event.set()
            return
        snapshot = self._broker_snapshot
        if not snapshot.tradability.get(decision.symbol, False):
            self.ledger.append(
                "broker_order_rejected",
                {
                    "idempotency_key": decision.idempotency_key,
                    "reason": "symbol is not broker-tradable",
                },
            )
            return
        if not snapshot.fractional_tradability.get(decision.symbol, False):
            self.ledger.append(
                "broker_order_rejected",
                {
                    "idempotency_key": decision.idempotency_key,
                    "reason": "fractional trading is unavailable for symbol",
                },
            )
            return
        target = target_for(decision, decision.symbol)
        if target is None:
            self.ledger.append(
                "broker_order_rejected",
                {
                    "idempotency_key": decision.idempotency_key,
                    "reason": "decision has no target for symbol",
                },
            )
            return
        funded_room = max(
            0.0,
            self.settings.max_capital
            - sum(
                item.quantity * item.average_cost for item in account.positions
            ),
        )
        order = plan_allocation_order(
            target=target,
            account=account,
            quote=quote,
            capabilities=BrokerCapabilities(
                supports_fractional_shares=True,
                quantity_increment=0.000001,
            ),
            max_notional=(
                None
                if decision.action is Action.SELL
                else min(self.settings.max_order_notional, funded_room)
            ),
        )
        if order is None or order.action is not decision.action:
            self.ledger.append(
                "broker_order_rejected",
                {
                    "idempotency_key": decision.idempotency_key,
                    "reason": "allocation drift produced no matching order",
                },
            )
            return

        ref_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"swagger-engine:{decision.idempotency_key}",
            )
        )
        self.ledger.append(
            "broker_order_plan",
            {
                "idempotency_key": decision.idempotency_key,
                "ref_id": ref_id,
                "order": order,
            },
        )
        try:
            preview = await self.robinhood.review_order(order, ref_id=ref_id)
        except (RobinhoodMCPError, OSError, ValueError) as exc:
            await self._stream_status(
                HealthState.HALTED, f"Robinhood order preview failed: {exc}"
            )
            self.stop_event.set()
            await self.stream.close()
            return
        self.ledger.append(
            "broker_order_preview",
            {
                "idempotency_key": decision.idempotency_key,
                "preview": preview,
            },
        )
        self.notifier.send(
            "Swagger order preview",
            f"{preview.side.upper()} {preview.quantity} {preview.symbol}; "
            f"alerts={len(preview.alerts)}",
        )
        if not preview.clear:
            self.ledger.append(
                "broker_order_rejected",
                {
                    "idempotency_key": decision.idempotency_key,
                    "reason": "broker preview returned alerts",
                    "alerts": preview.alerts,
                },
            )
            return
        if self.settings.mode == "preview":
            return

        try:
            submitted = await self.robinhood.place_reviewed_order(order, preview)
            self.ledger.append(
                "broker_order_submitted",
                {
                    "idempotency_key": decision.idempotency_key,
                    "result": submitted,
                },
            )
            result = await self._poll_order(submitted)
        except (RobinhoodMCPError, OSError, ValueError) as exc:
            await self._stream_status(
                HealthState.HALTED, f"Robinhood order lifecycle failed: {exc}"
            )
            self.stop_event.set()
            await self.stream.close()
            return
        self.ledger.append(
            "broker_order_terminal",
            {
                "idempotency_key": decision.idempotency_key,
                "result": result,
            },
        )
        self.notifier.send(
            "Swagger broker result",
            f"{result.symbol} order is {result.state}; "
            f"filled={result.filled_quantity:g}",
        )
        try:
            reconciled = await self.robinhood.snapshot(self.settings.symbols)
            self._broker_snapshot = reconciled
            self.ledger.append(
                "post_order_broker_snapshot", reconciled.audit_payload()
            )
            if (
                not reconciled.positions
                and reconciled.portfolio_value <= self.settings.account_floor
            ):
                await self._stream_status(
                    HealthState.HALTED,
                    "account floor exit completed; engine locked",
                )
                self.stop_event.set()
                await self.stream.close()
            elif (
                not reconciled.positions
                and reconciled.portfolio_value >= self.settings.account_goal
            ):
                await self._stream_status(
                    HealthState.HALTED,
                    "account goal exit completed; engine locked",
                )
                self.stop_event.set()
                await self.stream.close()
        except (RobinhoodMCPError, OSError, ValueError) as exc:
            await self._stream_status(
                HealthState.HALTED, f"post-order reconciliation failed: {exc}"
            )
            self.stop_event.set()
            await self.stream.close()

    async def _poll_order(self, submitted: BrokerOrderResult) -> BrokerOrderResult:
        terminal = {"filled", "cancelled", "rejected", "failed", "voided", "expired"}
        deadline = time.monotonic() + self.settings.order_timeout_seconds
        latest = submitted
        while time.monotonic() < deadline:
            latest = await self.robinhood.get_order(submitted.order_id)  # type: ignore[union-attr]
            self.ledger.append("broker_order_status", latest)
            if latest.state.lower() in terminal:
                return latest
            await asyncio.sleep(self.settings.order_poll_seconds)
        # A timed-out real order is cancelled before the engine halts. The
        # cancellation method is unavailable in preview/read-only modes.
        await self.robinhood.cancel_order(submitted.order_id)  # type: ignore[union-attr]
        raise RobinhoodMCPError("order did not reach a terminal state before timeout")

    async def run(self) -> None:
        self.ledger.append(
            "engine_started",
            {
                "mode": self.settings.mode,
                "broker_mode": self.settings.broker_mode,
                "symbols": self.settings.symbols,
            },
        )
        if self.robinhood is not None:
            try:
                await self.robinhood.__aenter__()
                self._broker_snapshot = await self.robinhood.snapshot(
                    self.settings.symbols
                )
                self.ledger.append(
                    "initial_broker_snapshot",
                    self._broker_snapshot.audit_payload(),
                )
            except (RobinhoodMCPError, OSError, ValueError) as exc:
                await self._stream_status(
                    HealthState.HALTED,
                    f"Robinhood startup verification failed: {exc}",
                )
                self.stop_event.set()
                if self.robinhood is not None:
                    await self.robinhood.__aexit__(None, None, None)
                self.ledger.append(
                    "engine_stopped", {"health": self.health.state.value}
                )
                return

        watchdog = asyncio.create_task(self._stale_watchdog())
        broker_reconciler = (
            asyncio.create_task(self._broker_reconciler())
            if self.robinhood is not None
            else None
        )

        async def consume() -> None:
            async for event in self.stream.events():
                if self.stop_event.is_set():
                    break
                await self._handle_event(event)

        consumer = asyncio.create_task(consume())
        stop_waiter = asyncio.create_task(self.stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {consumer, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if consumer in done:
                await consumer
        finally:
            self.stop_event.set()
            tasks = [consumer, stop_waiter, watchdog]
            if broker_reconciler is not None:
                tasks.append(broker_reconciler)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.stream.close()
            if self.robinhood is not None:
                await self.robinhood.__aexit__(None, None, None)
            self.ledger.append("engine_stopped", {"health": self.health.state.value})


async def _run(settings: Settings, tracker: HealthTracker) -> HealthState:
    engine = ShadowEngine(settings, tracker)
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, engine.stop_event.set)
    await engine.run()
    return tracker.state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-port", type=int, default=None)
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        if args.health_port is not None:
            settings = replace(settings, health_port=args.health_port)
        settings.validate(require_market_data=True)
    except ConfigurationError as exc:
        print(f"configuration error: {exc}")
        return 2

    tracker = HealthTracker()
    server = HealthServer(settings.health_host, settings.health_port, tracker)
    server.start()
    try:
        final_state = asyncio.run(_run(settings, tracker))
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()
    if (
        final_state is HealthState.HALTED
        and not settings.kill_switch_path.exists()
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
