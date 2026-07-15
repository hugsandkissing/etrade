"""Always-on shadow engine. It has no real order execution path."""

from __future__ import annotations

import argparse
import asyncio
import signal
from collections import defaultdict, deque
from dataclasses import replace
from datetime import timedelta

from .bar_aggregator import SignalAggregator
from .broker import FailClosedMockBroker
from .config import ConfigurationError, Settings
from .decision_engine import DecisionContext, RuleBasedDecisionProvider
from .health import HealthServer, HealthTracker
from .ledger import AuditLedger
from .market_hours import is_regular_session
from .market_stream import AlpacaWebSocketStream
from .models import Action, EventType, HealthState, Quote, Signal
from .notifier import configure_logging, emit
from .risk import RiskContext, RiskKernel
from .shadow import ShadowPortfolio, ShadowPortfolioError


class ShadowEngine:
    def __init__(self, settings: Settings, health: HealthTracker | None = None):
        self.settings = settings
        self.settings.validate(require_market_data=True)
        self.logger = configure_logging()
        self.health = health or HealthTracker()
        self.ledger = AuditLedger(settings.ledger_path)
        self.portfolio = ShadowPortfolio(
            settings.state_path, settings.starting_cash, settings.shadow_slippage_bps
        )
        self.aggregator = SignalAggregator(settings)
        self.provider = RuleBasedDecisionProvider(settings)
        self.risk = RiskKernel(settings)
        self.broker = FailClosedMockBroker()
        self.stream = AlpacaWebSocketStream(settings, self._stream_status)
        self.signal_buffer: dict[str, deque[Signal]] = defaultdict(
            lambda: deque(maxlen=32)
        )
        self.seen_event_ids = {
            record.get("payload", {}).get("event_id")
            for record in self.ledger.records()
            if record.get("type") == "market_event"
        }
        self.seen_event_ids.discard(None)
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

    def _quotes(self) -> dict[str, Quote]:
        return {
            symbol: quote
            for symbol in self.settings.symbols
            if (quote := self.aggregator.current_quote(symbol)) is not None
        }

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
            if self.health.state is HealthState.HEALTHY and self.aggregator.stale():
                await self._stream_status(HealthState.DEGRADED, "market data is stale")

    async def _handle_event(self, event) -> None:
        if event.event_id in self.seen_event_ids:
            return
        self.seen_event_ids.add(event.event_id)
        self.ledger.append("market_event", event)
        signals = self.aggregator.process(event)
        if event.event_type is EventType.BAR:
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
        account = self.portfolio.account_state(self._quotes())
        decision = await self.provider.propose(
            DecisionContext(
                symbol=event.symbol,
                timestamp=event.timestamp,
                signals=recent,
                quote=self.aggregator.current_quote(event.symbol),
                position=self.portfolio.position(event.symbol),
                buying_power=account.buying_power,
                account_value=account.value,
            )
        )
        duplicate_key = self.ledger.contains_idempotency_key(decision.idempotency_key)
        self.ledger.append("decision", decision)
        if decision.action is Action.HOLD:
            return

        quote = self.aggregator.current_quote(event.symbol)
        verdict = self.risk.evaluate(
            decision,
            RiskContext(
                account=account,
                quote=quote,
                health=self.health.state,
                ledger_writable=self.ledger.writable(),
                duplicate_idempotency_key=duplicate_key,
            ),
        )
        self.ledger.append(
            "risk_verdict",
            {"idempotency_key": decision.idempotency_key, **verdict.__dict__},
        )
        if not verdict.approved or quote is None:
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
        updated = self.portfolio.position(event.symbol)
        if updated:
            self.aggregator.set_position_average_cost(
                updated.symbol, updated.average_cost
            )
        emit(self.logger, "shadow_fill", fill=fill)

    async def run(self) -> None:
        self.ledger.append(
            "engine_started",
            {
                "mode": self.settings.mode,
                "broker_mode": self.settings.broker_mode,
                "symbols": self.settings.symbols,
            },
        )
        watchdog = asyncio.create_task(self._stale_watchdog())

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
            for task in (consumer, stop_waiter, watchdog):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                consumer, stop_waiter, watchdog, return_exceptions=True
            )
            await self.stream.close()
            self.ledger.append("engine_stopped", {"health": self.health.state.value})


async def _run(settings: Settings, tracker: HealthTracker) -> None:
    engine = ShadowEngine(settings, tracker)
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, engine.stop_event.set)
    await engine.run()


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
        asyncio.run(_run(settings, tracker))
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
