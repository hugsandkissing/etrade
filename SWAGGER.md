# Swagger Engine v0.1

Swagger Engine is an always-on, staged **shadow/preview/live** service. It watches live
market data, generates structured hypothetical decisions, applies deterministic
risk rules, and records the complete process in an append-only audit ledger.

Shadow mode cannot touch orders. Preview mode can ask Robinhood to review a
proposed order but cannot place it. Live placement code exists behind explicit,
independent startup gates and is disabled by default.

## Safety boundary

The active path is intentionally separate from the legacy E*TRADE tooling and
the original `challenge/ledger.json` experiment:

```text
Alpaca IEX WebSocket
        |
Signal aggregator
        |
Rule-based decision provider
        |
Deterministic risk kernel
        |
Target allocation planner
        |
Broker-capability rounding + per-order notional cap
        |
Hash-chained JSONL ledger + report

Robinhood official MCP (mode-scoped)
        |
Pinned Agentic account snapshot
        |
Shadow-vs-real reconciliation ledger
```

Hard startup rules:

- Valid pairs are `shadow` + (`mock` or `robinhood_readonly`),
  `preview` + `robinhood_preview`, and `live` + `robinhood_live`.
- The mock broker refuses all broker calls.
- Robinhood tool access is fixed at process startup. Preview adds only
  `review_equity_order`; live adds only placement and cancellation. There is no
  generic public tool-call method.
- Live startup requires an explicit enable flag, passage of the launch
  date/time, and a local 0600 arming file containing `SWAGGER_LIVE_V1`.
- Before an order, the engine refreshes broker account, position, open-order,
  quote, tradability, and fractional-tradability state.
- Each entry/rotation order is capped at $10; protective exits may close the
  full fractional position. Every order is reviewed first, given a
  deterministic UUID, polled to a terminal state, and reconciled afterward.
- The engine halts new decisions when health is degraded or halted.
- Shadow decisions and fills are restricted to 9:30am–4:00pm US Eastern on weekdays.
- The filesystem kill switch halts the process.
- Missing credentials, stale quotes, duplicate proposals, wide spreads,
  breached loss limits, and unwritable audit storage fail closed.

External prices can trigger an evaluation, but only fresh Robinhood data can
authorize preview or execution.

## Local setup

Use Python 3.11 or newer:

```bash
cd ~/etrade
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the example only if a local `.env` does not already exist:

```bash
cp -n .env.example .env
```

Set the Alpaca market-data values locally. Never paste them into chat or commit
the `.env` file:

```dotenv
SWAGGER_MODE=shadow
SWAGGER_BROKER_MODE=mock
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
```

The default free Alpaca feed is IEX and monitors `VG,SPY,QQQ,XLE`. IEX is only a
subset of consolidated US trading activity, so it is suitable for shadow
validation, not production execution pricing.

## Start shadow mode

```bash
python -m swagger.engine --health-port 8080
```

The service binds health checks to localhost only:

```bash
curl http://127.0.0.1:8080/health
```

Stop gracefully with `Control-C`.

## Add read-only Robinhood reconciliation

This is a separate OAuth grant for the persistent Python process. It does not
copy, inspect, or reuse ChatGPT/Codex credentials. OAuth tokens, refresh tokens,
dynamic client registration, and the pinned Agentic account number are stored
in macOS Keychain.

First run the Robinhood-only smoke test. It does not require Alpaca keys:

```bash
python -m swagger.robinhood_smoke_test
```

On first use, the default browser opens Robinhood's official OAuth page and the
callback returns only to `127.0.0.1:8765`. Do not paste the authorization URL,
callback, token, or full account number into chat. The test calls only:

- `get_accounts`
- `get_portfolio`
- `get_equity_positions`
- `get_equity_orders`
- `get_equity_quotes`
- `get_equity_tradability`

The uniquely active, non-default Agentic cash account is pinned in Keychain.
Subsequent runs reject a missing, changed, ambiguous, default, or non-cash
account.

After a passing smoke test, opt into reconciliation in the untracked `.env`:

```dotenv
SWAGGER_BROKER_MODE=robinhood_readonly
BROKER_RECONCILE_SECONDS=300
```

Restart the shadow engine. It will append masked read-only snapshots and
shadow-vs-real position discrepancies to the ledger. If broker verification
fails, the engine halts new shadow decisions. This mode still has no order
preview, placement, cancellation, options, watchlist, scanner, deposit, or
withdrawal capability.

## Preview stage

Preview mode uses the real Agentic account as portfolio truth and asks Robinhood
to review allocation-derived orders. It never calls placement or cancellation:

```dotenv
SWAGGER_MODE=preview
SWAGGER_BROKER_MODE=robinhood_preview
MAX_ORDER_NOTIONAL=10
```

Each clear or alerted preview is written to the ledger and surfaced as a local
macOS notification. Restore the two shadow defaults to leave preview mode.

## Live safety gates

Live execution remains off unless every startup gate passes. Defaults:

```dotenv
SWAGGER_LIVE_ENABLED=false
SWAGGER_LIVE_NOT_BEFORE=2026-08-10T13:30:00Z
SWAGGER_LIVE_ARMING_FILE=swagger_state/LIVE_ARMED
```

Do not create the arming file until `swagger.readiness` passes. Live orders are
regular-hours market orders with fractional quantities rounded down to six
decimal places. A preview containing any alert is rejected. Duplicate or open
orders, stale data, reconciliation failure, or an unwritable ledger halt the
execution path. Options, watchlist/scanner mutation, deposits, and withdrawals
are absent from every allowlist.

## Audit and state

Runtime files are local and gitignored:

- `swagger_state/ledger.jsonl` — active append-only, hash-chained audit segment.
- `swagger_state/ledger_archive/` — immutable rotated ledger segments.
- `swagger_state/shadow_state.json` — recoverable hypothetical portfolio state.
- `swagger_state/KILL_SWITCH` — presence halts the engine.

To activate the kill switch:

```bash
touch swagger_state/KILL_SWITCH
```

Remove it only after reviewing why the engine halted:

```bash
rm swagger_state/KILL_SWITCH
```

Corrections to the ledger must be appended as `correction` records. Never edit
or delete prior JSONL lines.

The engine does not persist every exchange tick. It records all bars, signals,
decisions, risk verdicts, fills, health changes, and broker reconciliations;
quotes are sampled once per symbol per configured interval, and raw trades are
used in memory but not written. The active segment rotates at 100 MiB by
default. Rotation renames the completed segment without rewriting it and starts
the next segment with a hash-linked `ledger_segment_started` record.

```dotenv
LEDGER_QUOTE_SAMPLE_SECONDS=60
LEDGER_MAX_BYTES=104857600
SWAGGER_LEDGER_ARCHIVE_DIR=swagger_state/ledger_archive
```

## Decision policy

The default provider is deterministic and requires bullish confirmation from at
least two distinct families (for example price plus volume) before proposing a
BUY. Two thresholds caused by the same underlying price move count as one
family. SELL proposals require immediate adverse evidence or confirmation from
at least two bearish families. A per-symbol cooldown prevents repeated
proposals.

Every non-HOLD proposal is expressed as a **complete** target portfolio
allocation and is evaluated again by the independent risk kernel. Strategy code
says, for example, "VG 50%, QQQ 30%, cash 20%" rather than "buy 1.89 shares."
All held symbols must be present, including a zero weight for a full exit. Cash
is implicit and intentional: `1 - sum(symbol target weights)`. The execution
adapter compares that target with the current realized allocation and translates
`target weight - realized weight` into an order at the latest execution-side
quote.

Entry and rotation execution is deliberately chunked: a target can remain 50%
while a single buy is capped by `MAX_ORDER_NOTIONAL` (hard-limited to $10 in
v1). The unfilled distance remains explicit as `remaining_target_value`; the
cap never silently changes the strategy target. SELL targets may close the
entire fractional position so the account floor and goal are not protected in
slow $10 increments.

The shadow and Robinhood adapters support fractional shares. Shadow allocations
are filled hypothetically at the ask for buys or bid for sells, plus configured
slippage. A whole-share adapter rounds down to its supported quantity
increment and reports the unexecuted rounding difference separately as
`execution_residual_cash`. That value is not the portfolio's intentional cash
allocation. This keeps portfolio intent broker-neutral: adapters own quantity
rounding, while strategy and risk continue to reason in weights and dollars.

The optional `OpenAIDecisionProvider` is a fail-closed placeholder and always
returns HOLD. It has no tool or broker access.

## Reports

Daily UTC report:

```bash
python -m swagger.report --date 2026-07-14
```

Cumulative report:

```bash
python -m swagger.report --cumulative
```

Reports stream the ledger rather than loading the full file into memory. By
default they include rotated archives; use `--active-only` for a fast diagnostic
of the current segment. They include evaluations, actionable proposals, risk outcomes, hypothetical fills, win rate,
expectancy, drawdown, profit factor, holding period, transaction costs, and
comparisons with VG and SPY when enough bar data exists. They also expose the
latest target allocation, realized allocation, target-minus-realized drift,
intentional target/realized cash, and execution-only residual cash. Allocation
weights are decimal fractions (`0.50` means 50%).

## Verification

```bash
python -m pytest -q
python -m compileall swagger tests
python -m swagger.report --cumulative
python -m swagger.readiness --since 2026-07-27
```

Readiness requires ten full clean sessions (at least 100 evaluations each),
three clear real broker previews, at least one broker-reconciliation day, no
preview alerts or failed terminal live orders, and passage of the time lock. A
nonzero exit status means no-go.

## Start automatically on macOS

The LaunchAgent contains no keys or account values. It starts this repository's
virtual-environment Python, which reads the gitignored `.env`:

```bash
python -m swagger.service install
python -m swagger.service status
```

Logs are in `swagger_state/service.stdout.log` and
`swagger_state/service.stderr.log`. Remove automatic startup with:

```bash
python -m swagger.service uninstall
```

Unexpected fail-closed halts restart after a throttle interval. A graceful stop
or filesystem kill-switch stop stays stopped.

## Container

The Docker image runs as an unprivileged user and does not contain `.env`:

```bash
docker build -t swagger-engine:shadow .
docker run --rm --env-file .env -p 127.0.0.1:8080:8080 \
  -v "$PWD/swagger_state:/app/swagger_state" swagger-engine:shadow
```

For container health checks, set `HEALTH_HOST=0.0.0.0`; local execution should
keep the safer default `127.0.0.1`.

## Known limitations

- The strategy is unproven; passing tests does not establish trading edge.
- Alpaca IEX data does not represent the full consolidated market.
- Alpaca news uses a separate stream and is not enabled in v0.1.
- The regular-hours gate is DST-aware but does not yet include an explicit US
  exchange-holiday calendar; on holidays the feed should produce no eligible bars.
- Robinhood OAuth currently targets local macOS Keychain; container deployment
  needs a separate supported secret-store design.
- Read-only reconciliation does not authorize execution and is not a price feed.
- Notifications are local macOS alerts only; no remote/mobile channel exists.
- The shadow state file is a cache; the append-only ledger is the audit source.

Do not arm live execution until the readiness command passes and the preview
ledger has been reviewed.
