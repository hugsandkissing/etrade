"""Official Robinhood MCP client with mode-scoped tools and Keychain OAuth."""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
import time
import uuid
import webbrowser
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import keyring
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl

from .models import (
    AccountState,
    BrokerOrderPreview,
    BrokerOrderResult,
    ExecutionOrder,
    Position,
    Quote,
)


ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"

# The adapter has no generic public tool-call method. This allowlist is also
# checked internally so future refactors cannot accidentally introduce a write.
READ_ONLY_TOOLS = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_tradability",
    }
)

PREVIEW_TOOLS = frozenset({"review_equity_order"})
LIVE_TOOLS = frozenset({"place_equity_order", "cancel_equity_order"})

OPEN_ORDER_STATES = frozenset(
    {
        "new",
        "queued",
        "confirmed",
        "unconfirmed",
        "partially_filled",
        "pending_cancelled",
        "locating",
    }
)


class RobinhoodMCPError(RuntimeError):
    """Raised when read-only Robinhood verification cannot be completed."""


def mask_account_number(account_number: str) -> str:
    return f"••••{account_number[-4:]}" if account_number else "••••"


class KeychainTokenStorage(TokenStorage):
    """Persist OAuth material in macOS Keychain, never files or environment."""

    def __init__(
        self,
        server_url: str = ROBINHOOD_MCP_URL,
        *,
        service_prefix: str = "swagger-engine-robinhood-mcp",
    ) -> None:
        suffix = hashlib.sha256(server_url.encode()).hexdigest()[:12]
        self.service = f"{service_prefix}-{suffix}"

    async def _get(self, name: str) -> str | None:
        return await asyncio.to_thread(keyring.get_password, self.service, name)

    async def _set(self, name: str, value: str) -> None:
        await asyncio.to_thread(keyring.set_password, self.service, name, value)

    async def get_tokens(self) -> OAuthToken | None:
        raw = await self._get("oauth_tokens")
        if not raw:
            return None
        payload = json.loads(raw)
        return OAuthToken.model_validate(payload["token"])

    async def set_tokens(self, tokens: OAuthToken) -> None:
        now = time.time()
        expires_at = now + tokens.expires_in if tokens.expires_in else None
        await self._set(
            "oauth_tokens",
            json.dumps(
                {
                    "token": tokens.model_dump(mode="json"),
                    "stored_at": now,
                    "expires_at": expires_at,
                },
                separators=(",", ":"),
            ),
        )

    async def token_expiry(self) -> float | None:
        raw = await self._get("oauth_tokens")
        if not raw:
            return None
        return json.loads(raw).get("expires_at")

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = await self._get("oauth_client")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._set("oauth_client", client_info.model_dump_json())

    async def get_pinned_account(self) -> str | None:
        return await self._get("agentic_account")

    async def set_pinned_account(self, account_number: str) -> None:
        await self._set("agentic_account", account_number)


class PersistentOAuthClientProvider(OAuthClientProvider):
    """Restore token expiry so refresh tokens survive process restarts."""

    async def _initialize(self) -> None:  # type: ignore[override]
        await super()._initialize()
        storage = self.context.storage
        if isinstance(storage, KeychainTokenStorage):
            self.context.token_expiry_time = await storage.token_expiry()


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]
        self.server.results.put((code, state, error))
        body = (
            b"Robinhood authorization received. You can close this tab and return "
            b"to Swagger Engine."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _CallbackServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.results: queue.Queue[tuple[str | None, str | None, str | None]] = (
            queue.Queue(maxsize=1)
        )


class LocalOAuthCallback:
    """Own the loopback OAuth callback without accepting remote connections."""

    def __init__(self, port: int = 8765, timeout_seconds: int = 300):
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.server: _CallbackServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    def _start(self) -> None:
        if self.server is not None:
            return
        self.server = _CallbackServer(self.port)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="robinhood-oauth-callback",
            daemon=True,
        )
        self.thread.start()

    async def redirect(self, authorization_url: str) -> None:
        self._start()
        print("Opening Robinhood in your browser for this Python client.")
        print("Do not paste the authorization URL or callback into chat or source code.")
        if not webbrowser.open(authorization_url):
            raise RobinhoodMCPError("could not open the OAuth authorization page")

    async def callback(self) -> tuple[str, str | None]:
        if self.server is None:
            raise RobinhoodMCPError("OAuth callback server was not started")
        try:
            code, state, error = await asyncio.to_thread(
                self.server.results.get, True, self.timeout_seconds
            )
        except queue.Empty as exc:
            raise RobinhoodMCPError("OAuth authorization timed out") from exc
        finally:
            self.close()
        if error:
            raise RobinhoodMCPError(f"Robinhood OAuth returned: {error}")
        if not code:
            raise RobinhoodMCPError("Robinhood OAuth callback omitted the code")
        return code, state

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None


def _tool_data(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        message = "Robinhood MCP returned an error"
        for item in getattr(result, "content", []):
            if getattr(item, "type", None) == "text":
                message = item.text
                break
        raise RobinhoodMCPError(message)

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        data = structured.get("data", structured)
        if isinstance(data, dict):
            return data

    for item in getattr(result, "content", []):
        if getattr(item, "type", None) != "text":
            continue
        try:
            payload = json.loads(item.text)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            data = payload.get("data", payload)
            if isinstance(data, dict):
                return data
    raise RobinhoodMCPError("Robinhood MCP returned no structured data")


@dataclass(frozen=True)
class RobinhoodReadOnlySnapshot:
    account_masked: str
    account_type: str
    portfolio_value: float
    buying_power: float
    positions: tuple[Position, ...]
    open_order_symbols: tuple[str, ...]
    quotes: tuple[Quote, ...]
    tradability: dict[str, bool]
    fractional_tradability: dict[str, bool]
    verified_at: datetime

    def audit_payload(self) -> dict[str, Any]:
        """Return only non-secret, non-token fields suitable for the ledger."""
        return {
            "account": self.account_masked,
            "account_type": self.account_type,
            "portfolio_value": self.portfolio_value,
            "buying_power": self.buying_power,
            "positions": [
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_cost": position.average_cost,
                }
                for position in self.positions
            ],
            "open_order_symbols": list(self.open_order_symbols),
            "quotes": [
                {
                    "symbol": quote.symbol,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "timestamp": quote.timestamp.isoformat(),
                }
                for quote in self.quotes
            ],
            "tradability": self.tradability,
            "fractional_tradability": self.fractional_tradability,
            "verified_at": self.verified_at.isoformat(),
        }


class RobinhoodMCPClient:
    """Fixed-method client whose MCP allowlist is selected at startup."""

    def __init__(
        self,
        *,
        account_number: str | None,
        server_url: str = ROBINHOOD_MCP_URL,
        callback_port: int = 8765,
        storage: TokenStorage | None = None,
        auto_select_account: bool = True,
        access_mode: str = "readonly",
    ) -> None:
        if server_url != ROBINHOOD_MCP_URL:
            raise RobinhoodMCPError("only Robinhood's official MCP URL is allowed")
        self.account_number = account_number
        self.server_url = server_url
        self.callback = LocalOAuthCallback(callback_port)
        self.storage = storage or KeychainTokenStorage(server_url)
        self.auto_select_account = auto_select_account
        if access_mode not in {"readonly", "preview", "live"}:
            raise RobinhoodMCPError("Robinhood access mode is invalid")
        self.access_mode = access_mode
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._available_tools: frozenset[str] = frozenset()
        self._selected_account: dict[str, Any] | None = None
        self._call_lock = asyncio.Lock()

    @property
    def allowed_tools(self) -> frozenset[str]:
        allowed = set(READ_ONLY_TOOLS)
        if self.access_mode in {"preview", "live"}:
            allowed.update(PREVIEW_TOOLS)
        if self.access_mode == "live":
            allowed.update(LIVE_TOOLS)
        return frozenset(allowed)

    async def __aenter__(self) -> "RobinhoodMCPClient":
        self._stack = AsyncExitStack()
        try:
            oauth = PersistentOAuthClientProvider(
                server_url=self.server_url,
                client_metadata=OAuthClientMetadata(
                    client_name=f"Swagger Engine {self.access_mode} broker client",
                    redirect_uris=[AnyUrl(self.callback.redirect_uri)],
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"],
                ),
                storage=self.storage,
                redirect_handler=self.callback.redirect,
                callback_handler=self.callback.callback,
                timeout=300,
            )
            http_client = await self._stack.enter_async_context(
                httpx.AsyncClient(auth=oauth, follow_redirects=True, timeout=60)
            )
            read, write, _ = await self._stack.enter_async_context(
                streamable_http_client(
                    self.server_url,
                    http_client=http_client,
                    terminate_on_close=False,
                )
            )
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            listed = await self._session.list_tools()
            self._available_tools = frozenset(tool.name for tool in listed.tools)
            missing = self.allowed_tools - self._available_tools
            if missing:
                raise RobinhoodMCPError(
                    f"Robinhood MCP is missing required tools: {sorted(missing)}"
                )
            if self.auto_select_account:
                await self.select_account()
            return self
        except BaseException:
            await self._close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self._close()

    async def _close(self) -> None:
        self.callback.close()
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None

    async def _call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.allowed_tools:
            raise RobinhoodMCPError(
                f"tool is not in the {self.access_mode} allowlist: {name}"
            )
        if self._session is None:
            raise RobinhoodMCPError("Robinhood MCP client is not connected")
        if name not in self._available_tools:
            raise RobinhoodMCPError(f"read tool is unavailable: {name}")
        async with self._call_lock:
            return _tool_data(await self._session.call_tool(name, arguments or {}))

    async def select_account(self) -> dict[str, Any]:
        data = await self._call("get_accounts")
        accounts = [row for row in data.get("accounts", []) if row]
        active_agentic = [
            row
            for row in accounts
            if row.get("agentic_allowed")
            and not row.get("deactivated")
            and not row.get("permanently_deactivated")
            and row.get("state") == "active"
        ]
        pinned_number = self.account_number
        if pinned_number is None and isinstance(self.storage, KeychainTokenStorage):
            pinned_number = await self.storage.get_pinned_account()
        if pinned_number:
            matches = [
                row
                for row in active_agentic
                if str(row.get("account_number")) == pinned_number
            ]
            if len(matches) != 1:
                raise RobinhoodMCPError(
                    "configured Agentic account was missing, inactive, or ambiguous"
                )
            selected = matches[0]
        elif len(active_agentic) == 1:
            selected = active_agentic[0]
        else:
            raise RobinhoodMCPError(
                "Agentic account was not uniquely identifiable "
                f"(accounts={len(accounts)}, active_agentic={len(active_agentic)})"
            )
        if selected.get("is_default"):
            raise RobinhoodMCPError("the selected Agentic account must not be the default account")
        if selected.get("type") != "cash":
            raise RobinhoodMCPError("the selected Agentic account must be cash-only")
        if isinstance(self.storage, KeychainTokenStorage):
            await self.storage.set_pinned_account(str(selected["account_number"]))
        self._selected_account = selected
        return selected

    def _account(self) -> dict[str, Any]:
        if self._selected_account is None:
            raise RobinhoodMCPError("Agentic account has not been selected")
        return self._selected_account

    async def snapshot(self, symbols: tuple[str, ...]) -> RobinhoodReadOnlySnapshot:
        account = self._account()
        number = str(account["account_number"])
        portfolio, positions, orders, quotes, tradability = await asyncio.gather(
            self._call("get_portfolio", {"account_number": number}),
            self._call("get_equity_positions", {"account_number": number}),
            self._call("get_equity_orders", {"account_number": number}),
            self._call("get_equity_quotes", {"symbols": list(symbols)}),
            self._call(
                "get_equity_tradability",
                {"account_number": number, "symbols": list(symbols[:10])},
            ),
        )
        parsed_positions = tuple(
            Position(
                symbol=str(row["symbol"]),
                quantity=float(row["quantity"]),
                average_cost=float(row.get("average_buy_price") or 0),
            )
            for row in positions.get("positions", [])
            if row and float(row.get("quantity") or 0) != 0
        )
        open_symbols = tuple(
            sorted(
                {
                    str(row["symbol"])
                    for row in orders.get("orders", [])
                    if row and row.get("state") in OPEN_ORDER_STATES
                }
            )
        )
        parsed_quotes: list[Quote] = []
        for result in quotes.get("results", []):
            quote = result.get("quote") if result else None
            if not quote or not quote.get("symbol"):
                continue
            bid_time = datetime.fromisoformat(
                str(quote["venue_bid_time"]).replace("Z", "+00:00")
            )
            ask_time = datetime.fromisoformat(
                str(quote["venue_ask_time"]).replace("Z", "+00:00")
            )
            parsed_quotes.append(
                Quote(
                    symbol=str(quote["symbol"]),
                    bid=float(quote["bid_price"]),
                    ask=float(quote["ask_price"]),
                    timestamp=max(bid_time, ask_time),
                    source="robinhood-mcp",
                )
            )
        tradeable = {
            str(row["symbol"]): bool(row.get("tradeable"))
            and row.get("state") == "active"
            for row in tradability.get("results", [])
            if row and row.get("symbol")
        }
        fractional = {
            str(row["symbol"]): bool(
                row.get("fractional_tradable")
                or row.get("fractional_tradeable")
                or row.get("fractional")
            )
            for row in tradability.get("results", [])
            if row and row.get("symbol")
        }
        buying_power = portfolio.get("buying_power") or {}
        return RobinhoodReadOnlySnapshot(
            account_masked=mask_account_number(number),
            account_type=str(account.get("brokerage_account_type", "unknown")),
            portfolio_value=float(portfolio["total_value"]),
            buying_power=float(buying_power.get("buying_power") or 0),
            positions=parsed_positions,
            open_order_symbols=open_symbols,
            quotes=tuple(parsed_quotes),
            tradability=tradeable,
            fractional_tradability=fractional,
            verified_at=datetime.now(timezone.utc),
        )

    async def account_state(self) -> AccountState:
        snapshot = await self.snapshot(())
        return AccountState(
            value=snapshot.portfolio_value,
            buying_power=snapshot.buying_power,
            positions=snapshot.positions,
            verified_at=snapshot.verified_at,
        )

    async def quote(self, symbol: str) -> Quote:
        snapshot = await self.snapshot((symbol,))
        for quote in snapshot.quotes:
            if quote.symbol == symbol:
                return quote
        raise RobinhoodMCPError(f"Robinhood returned no quote for {symbol}")

    async def has_open_order(self, symbol: str) -> bool:
        snapshot = await self.snapshot((symbol,))
        return symbol in snapshot.open_order_symbols

    @staticmethod
    def _quantity(value: float) -> str:
        # Fractional market orders support at most six decimal places. Always
        # round down so serialization cannot increase order notional.
        units = int(value * 1_000_000)
        rendered = f"{units / 1_000_000:.6f}".rstrip("0").rstrip(".")
        if not rendered or rendered == "0":
            raise RobinhoodMCPError("order quantity rounds to zero")
        return rendered

    def _order_arguments(self, order: ExecutionOrder) -> dict[str, Any]:
        account = self._account()
        return {
            "account_number": str(account["account_number"]),
            "symbol": order.symbol,
            "side": order.action.value.lower(),
            "type": "market",
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
            "quantity": self._quantity(order.quantity),
        }

    async def review_order(
        self, order: ExecutionOrder, *, ref_id: str | None = None
    ) -> BrokerOrderPreview:
        if self.access_mode not in {"preview", "live"}:
            raise RobinhoodMCPError("order review requires preview or live access")
        ref_id = ref_id or str(uuid.uuid4())
        data = await self._call("review_equity_order", self._order_arguments(order))
        raw_alerts = data.get("alerts") or data.get("warnings") or []
        alerts: list[str] = []
        for alert in raw_alerts:
            if isinstance(alert, str):
                alerts.append(alert)
            elif isinstance(alert, dict):
                alerts.append(
                    str(
                        alert.get("message")
                        or alert.get("title")
                        or alert.get("code")
                        or "unspecified broker alert"
                    )
                )
            else:
                alerts.append(str(alert))
        return BrokerOrderPreview(
            ref_id=ref_id,
            symbol=order.symbol,
            side=order.action.value.lower(),
            quantity=self._quantity(order.quantity),
            estimated_notional=order.estimated_value,
            alerts=tuple(alerts),
        )

    async def place_reviewed_order(
        self, order: ExecutionOrder, preview: BrokerOrderPreview
    ) -> BrokerOrderResult:
        if self.access_mode != "live":
            raise RobinhoodMCPError("real order placement requires live access")
        if not preview.clear:
            raise RobinhoodMCPError("broker preview contains alerts")
        if preview.symbol != order.symbol or preview.side != order.action.value.lower():
            raise RobinhoodMCPError("preview does not match the proposed order")
        arguments = self._order_arguments(order)
        arguments["ref_id"] = preview.ref_id
        data = await self._call("place_equity_order", arguments)
        order_payload = data.get("order") if isinstance(data.get("order"), dict) else data
        order_id = str(order_payload.get("order_id") or order_payload.get("id") or "")
        if not order_id:
            raise RobinhoodMCPError("Robinhood placement response omitted order id")
        return BrokerOrderResult(
            ref_id=preview.ref_id,
            order_id=order_id,
            symbol=order.symbol,
            state=str(order_payload.get("state") or "submitted"),
        )

    async def get_order(self, order_id: str) -> BrokerOrderResult:
        account = self._account()
        data = await self._call(
            "get_equity_orders",
            {
                "account_number": str(account["account_number"]),
                "order_id": order_id,
            },
        )
        orders = [item for item in data.get("orders", []) if item]
        if len(orders) != 1:
            raise RobinhoodMCPError("broker order was missing or ambiguous")
        item = orders[0]
        return BrokerOrderResult(
            ref_id=str(item.get("ref_id") or ""),
            order_id=str(item.get("order_id") or item.get("id") or order_id),
            symbol=str(item.get("symbol") or ""),
            state=str(item.get("state") or "unknown"),
            filled_quantity=float(item.get("cumulative_quantity") or item.get("filled_quantity") or 0),
            average_price=(
                float(item.get("average_price"))
                if item.get("average_price") is not None
                else None
            ),
        )

    async def cancel_order(self, order_id: str) -> BrokerOrderResult:
        if self.access_mode != "live":
            raise RobinhoodMCPError("order cancellation requires live access")
        account = self._account()
        await self._call(
            "cancel_equity_order",
            {
                "account_number": str(account["account_number"]),
                "order_id": order_id,
            },
        )
        return await self.get_order(order_id)
