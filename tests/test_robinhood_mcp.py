import asyncio

import pytest
from mcp.shared.auth import OAuthToken
from mcp.types import CallToolResult

from swagger.robinhood_mcp import (
    LIVE_TOOLS,
    READ_ONLY_TOOLS,
    KeychainTokenStorage,
    RobinhoodMCPClient,
    RobinhoodMCPError,
    mask_account_number,
)
from swagger.models import Action, ExecutionOrder


def tool_result(data):
    return CallToolResult(content=[], structuredContent={"data": data})


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_equity_orders" and arguments.get("order_id"):
            return tool_result(
                {
                    "orders": [
                        {
                            "order_id": arguments["order_id"],
                            "ref_id": "fixed-ref",
                            "symbol": "VG",
                            "state": "filled",
                            "cumulative_quantity": "0.75",
                            "average_price": "13.25",
                        }
                    ]
                }
            )
        responses = {
            "get_accounts": {
                "accounts": [
                    {
                        "account_number": "11112222",
                        "agentic_allowed": False,
                        "brokerage_account_type": "individual",
                        "deactivated": False,
                        "permanently_deactivated": False,
                        "is_default": True,
                        "state": "active",
                        "type": "margin",
                    },
                    {
                        "account_number": "33334444",
                        "agentic_allowed": True,
                        "brokerage_account_type": "individual",
                        "deactivated": False,
                        "permanently_deactivated": False,
                        "is_default": False,
                        "state": "active",
                        "type": "cash",
                    },
                ]
            },
            "get_portfolio": {
                "total_value": "50.25",
                "buying_power": {"buying_power": "0.25"},
            },
            "get_equity_positions": {
                "positions": [
                    {
                        "symbol": "VG",
                        "quantity": "3.5",
                        "average_buy_price": "13.20",
                    }
                ]
            },
            "get_equity_orders": {
                "orders": [
                    {"symbol": "VG", "state": "filled"},
                    {"symbol": "SPY", "state": "queued"},
                ]
            },
            "get_equity_quotes": {
                "results": [
                    {
                        "quote": {
                            "symbol": "VG",
                            "bid_price": "13.24",
                            "ask_price": "13.25",
                            "venue_bid_time": "2026-07-14T20:00:00Z",
                            "venue_ask_time": "2026-07-14T20:00:01Z",
                        }
                    }
                ]
            },
            "get_equity_tradability": {
                "results": [
                    {
                        "symbol": "VG",
                        "tradeable": True,
                        "fractional_tradable": True,
                        "state": "active",
                    }
                ]
            },
            "review_equity_order": {"alerts": []},
            "place_equity_order": {
                "order": {
                    "order_id": "order-123",
                    "state": "queued",
                }
            },
            "cancel_equity_order": {"cancelled": True},
        }
        return tool_result(responses[name])


class DummyTokenStorage:
    async def get_tokens(self):
        return None

    async def set_tokens(self, tokens):
        return None

    async def get_client_info(self):
        return None

    async def set_client_info(self, client_info):
        return None


def connected_client(account_number="33334444", access_mode="readonly"):
    client = RobinhoodMCPClient(
        account_number=account_number,
        storage=DummyTokenStorage(),
        access_mode=access_mode,
    )
    client._session = FakeSession()
    client._available_tools = client.allowed_tools
    return client


def test_mask_account_number():
    assert mask_account_number("1234567890") == "••••7890"


def test_account_selection_and_snapshot_are_pinned_and_read_only():
    async def scenario():
        client = connected_client()
        selected = await client.select_account()
        assert selected["account_number"] == "33334444"
        snapshot = await client.snapshot(("VG",))
        assert snapshot.account_masked == "••••4444"
        assert snapshot.portfolio_value == 50.25
        assert snapshot.positions[0].average_cost == 13.2
        assert snapshot.open_order_symbols == ("SPY",)
        assert snapshot.tradability == {"VG": True}
        assert snapshot.fractional_tradability == {"VG": True}
        assert snapshot.quotes[0].source == "robinhood-mcp"

        calls = client._session.calls
        assert calls[0] == ("get_accounts", {})
        assert all(name in READ_ONLY_TOOLS for name, _ in calls)
        for name, arguments in calls[1:]:
            if "account_number" in arguments:
                assert arguments["account_number"] == "33334444"

    asyncio.run(scenario())


def test_wrong_account_and_mutating_tool_fail_closed():
    async def scenario():
        client = connected_client("does-not-exist")
        with pytest.raises(RobinhoodMCPError, match="missing, inactive, or ambiguous"):
            await client.select_account()

        client = connected_client()
        with pytest.raises(RobinhoodMCPError, match="readonly allowlist"):
            await client._call("place_equity_order", {"symbol": "VG"})
        assert client._session.calls == []

    asyncio.run(scenario())


def test_keychain_storage_round_trip_preserves_expiry():
    class MemoryStorage(KeychainTokenStorage):
        def __init__(self):
            super().__init__()
            self.values = {}

        async def _get(self, name):
            return self.values.get(name)

        async def _set(self, name, value):
            self.values[name] = value

    async def scenario():
        storage = MemoryStorage()
        await storage.set_tokens(
            OAuthToken(
                access_token="test-access",
                refresh_token="test-refresh",
                expires_in=3600,
            )
        )
        restored = await storage.get_tokens()
        assert restored is not None
        assert restored.access_token == "test-access"
        assert await storage.token_expiry() is not None
        await storage.set_pinned_account("33334444")
        assert await storage.get_pinned_account() == "33334444"

    asyncio.run(scenario())


def test_preview_is_non_mutating_and_live_reuses_review_ref_id():
    order = ExecutionOrder(
        symbol="VG",
        action=Action.BUY,
        quantity=0.7500009,
        estimated_price=13.25,
        estimated_value=9.9375,
        target_weight=0.5,
        residual_cash=0.0,
    )

    async def scenario():
        preview_client = connected_client(access_mode="preview")
        await preview_client.select_account()
        preview = await preview_client.review_order(order, ref_id="fixed-ref")
        assert preview.clear
        assert preview.quantity == "0.75"
        with pytest.raises(RobinhoodMCPError, match="requires live access"):
            await preview_client.place_reviewed_order(order, preview)
        assert not any(name in LIVE_TOOLS for name, _ in preview_client._session.calls)

        live_client = connected_client(access_mode="live")
        await live_client.select_account()
        live_preview = await live_client.review_order(order, ref_id="fixed-ref")
        submitted = await live_client.place_reviewed_order(order, live_preview)
        terminal = await live_client.get_order(submitted.order_id)
        assert submitted.order_id == "order-123"
        assert terminal.state == "filled"
        placement = next(
            arguments
            for name, arguments in live_client._session.calls
            if name == "place_equity_order"
        )
        assert placement["ref_id"] == "fixed-ref"
        assert placement["market_hours"] == "regular_hours"
        assert placement["type"] == "market"
        assert placement["quantity"] == "0.75"

    asyncio.run(scenario())
