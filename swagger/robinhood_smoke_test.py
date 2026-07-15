"""Sanitized, read-only smoke test for the standalone Robinhood MCP client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

from .robinhood_mcp import ROBINHOOD_MCP_URL, RobinhoodMCPClient, RobinhoodMCPError


async def run(symbol: str) -> dict[str, object]:
    load_dotenv()
    account_number = os.getenv("ROBINHOOD_ACCOUNT_NUMBER") or None
    callback_port = int(os.getenv("ROBINHOOD_OAUTH_CALLBACK_PORT", "8765"))
    async with RobinhoodMCPClient(
        account_number=account_number,
        server_url=ROBINHOOD_MCP_URL,
        callback_port=callback_port,
    ) as client:
        snapshot = await client.snapshot((symbol,))
    position = next(
        (row for row in snapshot.positions if row.symbol == symbol), None
    )
    quote = next((row for row in snapshot.quotes if row.symbol == symbol), None)
    return {
        "passed": bool(position and quote and snapshot.tradability.get(symbol)),
        "account": snapshot.account_masked,
        "account_type": snapshot.account_type,
        "portfolio_readable": True,
        "buying_power_readable": True,
        "symbol": symbol,
        "position_found": position is not None,
        "open_order": symbol in snapshot.open_order_symbols,
        "quote_found": quote is not None,
        "tradeable": snapshot.tradability.get(symbol, False),
        "mutating_tools_available_to_client": False,
        "mutating_tools_called": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="VG")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.symbol.upper()))
    except (RobinhoodMCPError, OSError, ValueError) as exc:
        result = {
            "passed": False,
            "error": str(exc),
            "mutating_tools_called": [],
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
