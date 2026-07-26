"""Environment-backed configuration with fail-closed startup validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


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
    max_order_notional: float = 10.0
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
    ledger_quote_sample_seconds: int = 60
    ledger_max_bytes: int = 100 * 1024 * 1024
    ledger_path: Path = Path("swagger_state/ledger.jsonl")
    ledger_archive_dir: Path = Path("swagger_state/ledger_archive")
    state_path: Path = Path("swagger_state/shadow_state.json")
    kill_switch_path: Path = Path("swagger_state/KILL_SWITCH")
    health_host: str = "127.0.0.1"
    health_port: int = 8080
    starting_cash: float = 50.0
    live_enabled: bool = False
    live_not_before: str = "2026-08-10T13:30:00Z"
    live_arming_path: Path = Path("swagger_state/LIVE_ARMED")
    notifications_enabled: bool = True
    order_poll_seconds: int = 2
    order_timeout_seconds: int = 120
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
            robinhood_oauth_callback_port=_int("ROBINHOOD_OAUTH_CALLBACK_PORT", 8765),
            broker_reconcile_seconds=_int("BROKER_RECONCILE_SECONDS", 300),
            max_capital=_float("MAX_CAPITAL", 50),
            max_order_notional=_float("MAX_ORDER_NOTIONAL", 10),
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
            ledger_quote_sample_seconds=_int("LEDGER_QUOTE_SAMPLE_SECONDS", 60),
            ledger_max_bytes=_int("LEDGER_MAX_BYTES", 100 * 1024 * 1024),
            ledger_path=Path(
                os.getenv("SWAGGER_LEDGER_PATH", "swagger_state/ledger.jsonl")
            ),
            ledger_archive_dir=Path(
                os.getenv(
                    "SWAGGER_LEDGER_ARCHIVE_DIR",
                    "swagger_state/ledger_archive",
                )
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
            live_enabled=_bool("SWAGGER_LIVE_ENABLED", False),
            live_not_before=os.getenv(
                "SWAGGER_LIVE_NOT_BEFORE", "2026-08-10T13:30:00Z"
            ),
            live_arming_path=Path(
                os.getenv("SWAGGER_LIVE_ARMING_FILE", "swagger_state/LIVE_ARMED")
            ),
            notifications_enabled=_bool("SWAGGER_NOTIFICATIONS_ENABLED", True),
            order_poll_seconds=_int("ORDER_POLL_SECONDS", 2),
            order_timeout_seconds=_int("ORDER_TIMEOUT_SECONDS", 120),
        )
        settings.validate(require_market_data=False)
        return settings

    def validate(self, *, require_market_data: bool = True) -> None:
        if self.mode not in {"shadow", "preview", "live"}:
            raise ConfigurationError("SWAGGER_MODE must be shadow, preview, or live")
        if self.broker_mode not in {
            "mock",
            "robinhood_readonly",
            "robinhood_preview",
            "robinhood_live",
        }:
            raise ConfigurationError(
                "SWAGGER_BROKER_MODE must be mock, robinhood_readonly, "
                "robinhood_preview, or robinhood_live"
            )
        allowed_pairs = {
            "shadow": {"mock", "robinhood_readonly"},
            "preview": {"robinhood_preview"},
            "live": {"robinhood_live"},
        }
        if self.broker_mode not in allowed_pairs[self.mode]:
            raise ConfigurationError(
                f"SWAGGER_MODE={self.mode} cannot use {self.broker_mode}"
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
        if not 0 < self.max_order_notional <= 10:
            raise ConfigurationError(
                "MAX_ORDER_NOTIONAL must be positive and no more than $10 in v1"
            )
        if self.max_positions < 1:
            raise ConfigurationError("MAX_POSITIONS must be positive")
        if self.stale_seconds < 1 or self.proposal_cooldown_seconds < 0:
            raise ConfigurationError("timing values must be non-negative")
        if self.ledger_quote_sample_seconds < 1:
            raise ConfigurationError("LEDGER_QUOTE_SAMPLE_SECONDS must be positive")
        if self.ledger_max_bytes < 1024 * 1024:
            raise ConfigurationError("LEDGER_MAX_BYTES must be at least 1 MiB")
        if self.order_poll_seconds < 1 or self.order_timeout_seconds < 10:
            raise ConfigurationError("order polling values are too small")
        if self.mode == "live":
            if not self.live_enabled:
                raise ConfigurationError("live mode requires SWAGGER_LIVE_ENABLED=true")
            try:
                not_before = datetime.fromisoformat(
                    self.live_not_before.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ConfigurationError(
                    "SWAGGER_LIVE_NOT_BEFORE must be ISO 8601"
                ) from exc
            if not_before.tzinfo is None:
                raise ConfigurationError("SWAGGER_LIVE_NOT_BEFORE must include timezone")
            if datetime.now(timezone.utc) < not_before.astimezone(timezone.utc):
                raise ConfigurationError(
                    f"live mode is time-locked until {self.live_not_before}"
                )
            if not self.live_arming_path.exists():
                raise ConfigurationError("live mode requires the local arming file")
            if self.live_arming_path.read_text().strip() != "SWAGGER_LIVE_V1":
                raise ConfigurationError("live arming file has invalid contents")
            if self.live_arming_path.stat().st_mode & 0o077:
                raise ConfigurationError("live arming file permissions must be 0600")
