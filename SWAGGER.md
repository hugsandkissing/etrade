# Swagger Engine v0.1

Swagger Engine is an always-on **shadow trading** service. It watches live
market data, generates structured hypothetical decisions, applies deterministic
risk rules, and records the complete process in an append-only audit ledger.

It cannot place, preview, cancel, or modify a real order. No live broker adapter
exists in this version.

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
Hypothetical bid/ask fill
        |
Hash-chained JSONL ledger + report
```

Hard startup rules:

- `SWAGGER_MODE` must be `shadow`.
- `SWAGGER_BROKER_MODE` must be `mock`.
- Live mode is not implemented.
- The mock broker refuses all broker calls.
- The engine halts new decisions when health is degraded or halted.
- Shadow decisions and fills are restricted to 9:30am–4:00pm US Eastern on weekdays.
- The filesystem kill switch halts the process.
- Missing credentials, stale quotes, duplicate proposals, wide spreads,
  breached loss limits, and unwritable audit storage fail closed.

External prices can trigger an evaluation, but they cannot authorize a real
fill. All fills in this version are hypothetical.

## Local setup

Use Python 3.11 or newer:

```bash
cd ~/etrade
source .venv/bin/activate
pip install -r requirements-dev.txt
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

## Audit and state

Runtime files are local and gitignored:

- `swagger_state/ledger.jsonl` — append-only, hash-chained audit events.
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

## Decision policy

The default provider is deterministic and requires bullish confirmation from at
least two distinct families (for example price plus volume) before proposing a
BUY. Two thresholds caused by the same underlying price move count as one
family. SELL proposals require immediate adverse evidence or confirmation from
at least two bearish families. A per-symbol cooldown prevents repeated
proposals.

Every non-HOLD proposal is evaluated again by the independent risk kernel.
Approved proposals are filled hypothetically at the ask for buys or bid for
sells, plus configured slippage. Fractional quantities are permitted in the
shadow book because the intended Robinhood cash account supports fractional
equity orders; this does not imply broker integration.

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

Reports include proposals, risk outcomes, hypothetical fills, win rate,
expectancy, drawdown, profit factor, holding period, transaction costs, and
comparisons with VG and SPY when enough bar data exists.

## Verification

```bash
python -m pytest -q
python -m compileall swagger tests
python -m swagger.report --cumulative
```

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
- No real Robinhood authentication or broker connection exists.
- No external notification delivery exists; events are logged locally.
- The shadow state file is a cache; the append-only ledger is the audit source.

Do not add live execution until shadow sessions, restart recovery, data-source
comparisons, and broker read-only reconciliation have been independently
validated.
