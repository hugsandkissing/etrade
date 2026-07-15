# Robinhood MCP integration

Swagger Engine uses Robinhood's official Trading MCP endpoint:

```text
https://agent.robinhood.com/mcp/trading
```

## Current stage: Codex plus standalone Python read-only authorization

The project-scoped `.codex/config.toml` enables only verified read tools. It
does not expose order review, placement, cancellation, options, watchlist
mutation, or scanner mutation tools.

Initial authorization is interactive and must be completed by the account
owner on a desktop. Never copy OAuth tokens into `.env`, source files, chat, or
GitHub.

From the repository:

```bash
cd ~/etrade
codex mcp login robinhood-trading
```

After authorization, restart Codex in this trusted project and inspect `/mcp`.

The Python runtime uses a separate OAuth client built with the official MCP
Python SDK. It stores tokens, refresh material, client registration, and the
pinned Agentic account in macOS Keychain:

```bash
source .venv/bin/activate
python -m swagger.robinhood_smoke_test
```

The browser callback binds to loopback only. The smoke output is sanitized and
never prints full account identifiers or credentials.

## Safety boundaries

- Robinhood read tools may expose all connected Robinhood accounts.
- Any adapter must pin the exact Agentic account identifier after discovery and
  reject missing or ambiguous matches.
- Read-only reconciliation comes before order preview or placement.
- The standalone Python engine does not inherit Codex's MCP connection; it uses
  its own official OAuth grant.
- No Codex token store may be scraped or copied into the Python service.
- Live order tools remain absent from Swagger Engine v0.1.

## Standalone Python allowlist

The Python client exposes exactly six fixed read methods:

- `get_accounts`
- `get_portfolio`
- `get_equity_positions`
- `get_equity_orders`
- `get_equity_quotes`
- `get_equity_tradability`

There is no API accepting arbitrary tool names. The internal call boundary
rejects any name outside this allowlist before contacting Robinhood.

## Planned integration stages

1. Authorize the project-scoped Codex MCP connection.
2. Run read-only calls for accounts, portfolio, positions, orders, quotes, and
   tradability; record only masked identifiers in diagnostics.
3. Verify the exact returned tool schemas and Agentic account marker.
4. Authorize the standalone official MCP OAuth client, with token storage in
   macOS Keychain.
5. Reconcile the real Agentic account against shadow state without placing
   orders.
6. Consider order review only after several clean shadow sessions.
7. Add order placement only under a separate explicit owner-approved milestone.

The ChatGPT/Codex connector and the Python runtime are separate clients. A
successful Codex authorization proves the Robinhood MCP account connection, but
does not by itself give the background Python process broker access.
