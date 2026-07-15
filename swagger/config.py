"""Environment-backed configuration with fail-closed startup validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    pass


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    mode: str = "shadow"
    broker_mode: str = "mock"
    symbols: tuple[str, ...] = ("VG", "SPY", "QQQ", "XLE")
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None
    alpaca_stream_url: str = "wss://stream.data.alpaca.markets/v2/iex"
    robinhood_account_number: str | None = None
    robinhood_mcp_url: str = "https://agent.robinhood.com/mcp/trading"
    robinhood_oauth_callback_port: int = 8765
    broker_reconcile_seconds: int = 300
    max_capital: float = 50.0
    account_floor: float = 40.0
    account_goal: float = 65.0
    max_positions: int = 2
    daily_loss_limit_pct: float = 5.0
    stale_seconds: int = 15
    max_spread_pct: float = 0.75
    min_five_minute_volume: float = 1_000.0
    proposal_cooldown_seconds: int = 300
    shadow_slippage_bps: float = 5.0
    reconnect_max_attempts: int = 8
    reconnect_max_seconds: int = 60
    ledger_path: Path = Path("swagger_state/ledger.jsonl")
    state_path: Path = Path("swagger_state/shadow_state.json")
    kill_switch_path: Path = Path("swagger_state/KILL_SWITCH")
    health_host: str = "127.0.0.1"
    health_port: int = 8080
    starting_cash: float = 50.0
    banned_symbols: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"TQQQ", "SQQQ", "SOXL", "SOXS", "BITX", "TSLL"}
        )
    )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        symbols = tuple(
            symbol.strip().upper()
            for symbol in os.getenv("SWAGGER_SYMBOLS", "VG,SPY,QQQ,XLE").split(",")
            if symbol.strip()
        )
        settings = cls(
            mode=os.getenv("SWAGGER_MODE", "shadow").lower(),
            broker_mode=os.getenv("SWAGGER_BROKER_MODE", "mock").lower(),
            symbols=symbols,
            alpaca_api_key=os.getenv("ALPACA_API_KEY"),
            alpaca_api_secret=os.getenv("ALPACA_API_SECRET"),
            alpaca_stream_url=os.getenv(
                "ALPACA_STREAM_URL", "wss://stream.data.alpaca.markets/v2/iex"
            ),
            robinhood_account_number=os.getenv("ROBINHOOD_ACCOUNT_NUMBER"),
            robinhood_mcp_url=os.getenv(
                "ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading"
            ),
            robinhood_oauth_callback_port=_int(
                "ROBINHOOD_OAUTH_CALLBACK_PORT", 8765
            ),
            broker_reconcile_seconds=_int("BROKER_RECONCILE_SECONDS", 300),
            max_capital=_float("MAX_CAPITAL", 50),
            account_floor=_float("ACCOUNT_FLOOR", 40),
            account_goal=_float("ACCOUNT_GOAL", 65),
            max_positions=_int("MAX_POSITIONS", 2),
            daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 5),
            stale_seconds=_int("MARKET_DATA_STALE_SECONDS", 15),
            max_spread_pct=_float("MAX_SPREAD_PCT", 0.75),
            min_five_minute_volume=_float("MIN_FIVE_MINUTE_VOLUME", 1_000),
            proposal_cooldown_seconds=_int("PROPOSAL_COOLDOWN_SECONDS", 300),
            shadow_slippage_bps=_float("SHADOW_SLIPPAGE_BPS", 5),
            reconnect_max_attempts=_int("RECONNECT_MAX_ATTEMPTS", 8),
            reconnect_max_seconds=_int("RECONNECT_MAX_SECONDS", 60),
            ledger_path=Path(
                os.getenv("SWAGGER_LEDGER_PATH", "swagger_state/ledger.jsonl")
            ),
            state_path=Path(
                os.getenv("SWAGGER_STATE_PATH", "swagger_state/shadow_state.json")
            ),
            kill_switch_path=Path(
                os.getenv("SWAGGER_KILL_SWITCH", "swagger_state/KILL_SWITCH")
            ),
            health_host=os.getenv("HEALTH_HOST", "127.0.0.1"),
            health_port=_int("HEALTH_PORT", 8080),
            starting_cash=_float("SHADOW_STARTING_CASH", 50),
        )
        settings.validate(require_market_data=False)
        return settings

    def validate(self, *, require_market_data: bool = True) -> None:
        if self.mode != "shadow":
            raise ConfigurationError("Only SWAGGER_MODE=shadow is implemented")
        if self.broker_mode not in {"mock", "robinhood_readonly"}:
            raise ConfigurationError(
                "SWAGGER_BROKER_MODE must be mock or robinhood_readonly"
            )
        if self.robinhood_mcp_url != "https://agent.robinhood.com/mcp/trading":
            raise ConfigurationError("only Robinhood's official MCP URL is allowed")
        if not 1024 <= self.robinhood_oauth_callback_port <= 65535:
            raise ConfigurationError(
                "ROBINHOOD_OAUTH_CALLBACK_PORT must be between 1024 and 65535"
            )
        if self.broker_reconcile_seconds < 30:
            raise ConfigurationError("BROKER_RECONCILE_SECONDS must be at least 30")
        if not self.symbols:
            raise ConfigurationError("SWAGGER_SYMBOLS cannot be empty")
        if require_market_data and (
            not self.alpaca_api_key or not self.alpaca_api_secret
        ):
            raise ConfigurationError(
                "Alpaca credentials are required to start the live shadow stream"
            )
        if self.account_floor >= self.account_goal:
            raise ConfigurationError("ACCOUNT_FLOOR must be below ACCOUNT_GOAL")
        if self.starting_cash > self.max_capital:
            raise ConfigurationError("SHADOW_STARTING_CASH cannot exceed MAX_CAPITAL")
        if self.max_positions < 1:
            raise ConfigurationError("MAX_POSITIONS must be positive")
        if self.stale_seconds < 1 or self.proposal_cooldown_seconds < 0:
            raise ConfigurationError("timing values must be non-negative")
