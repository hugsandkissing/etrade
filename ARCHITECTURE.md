# System architecture — E*TRADE tools + $100→$200 paper-trading challenge

Audience: another AI/engineer working on this codebase. Everything here is
current as of 2026-07-14. Read this plus README-MONITORING.md and
STRATEGY.md before changing behavior. The git history is the audit trail —
never rewrite it, and never quietly restate ledger data (see Invariants).

## What this system is

Two loosely coupled subsystems in one repo:

1. **E*TRADE API tooling** (`etrade_client.py`, `auth.py`, `example.py`,
   `orders.py`, `dashboard.py`) — talks to E*TRADE's REST API via OAuth
   1.0a. Currently pointed at the SANDBOX (fake data). A production key is
   pending approval; nothing may place real orders until the owner
   explicitly flips that switch.
2. **Paper-trading challenge** (`challenge.py`, `watch.py`,
   `report_data.py`, `challenge/` data dir, `reports/`) — a simulated $100
   account, goal $200, scored against REAL market prices from Yahoo. This
   is fully independent of the E*TRADE API and is where all "trading"
   currently happens.

An agent (Claude, in a persistent Claude Code cloud session) makes all
trade decisions; the scripts are its hands. Scheduled wake-ups (below)
drive autonomous operation.

## Files

| File | Role |
|---|---|
| `etrade_client.py` | E*TRADE OAuth session + config from env vars (`ETRADE_CONSUMER_KEY`, `ETRADE_CONSUMER_SECRET`, `ETRADE_SANDBOX`). Tokens cached in `tokens.json` (gitignored, expire midnight ET daily). |
| `auth.py` | Interactive daily OAuth login (browser URL + verification code). |
| `example.py` | Smoke test: accounts + quote. |
| `orders.py` | E*TRADE two-step order flow (preview → confirm → place). Sandbox returns canned responses regardless of input. |
| `dashboard.py` | Generates `dashboard.html` (gitignored): E*TRADE account view + challenge panel. Published as a claude.ai artifact by the agent; the page self-reloads every 60s but data only changes when republished. |
| `challenge.py` | Paper ledger CLI: `init`, `buy`, `sell`, `mark`, `status`. Enforces whole shares, long only, non-negative cash. All writes serialized via `flock` on `challenge/.ledger.lock`. `record_mark()` is importable. |
| `watch.py` | Guardrail watcher daemon: polls Yahoo every `--interval` (30s), exits code 2 with JSON alert if any position crosses its stop/target band; auto-appends a ledger mark every `--mark-every` (300s). Runs as a background process during market hours. |
| `report_data.py` | Dumps a day's marks/trades/moves as JSON for the daily report. |
| `live_dashboard.py` | Localhost real-time viewer (owner runs it on their own machine): stdlib HTTP server, page polls `/quotes` every 10s, server proxies Yahoo. Falls back to last ledger mark with a loud STALE banner — never silently. |
| `challenge/ledger.json` | Source of truth: cash, positions, every trade (with rationale + price source), every valuation mark. COMMITTED to git deliberately for auditability. |
| `challenge/guardrails.json` | Per-symbol stop/target bands, % relative to avg cost. `_default` key for new positions. |
| `challenge/overnight-notes.md` | Dated signal log written by overnight/pre-market runs; consumed by the daily report. |
| `reports/YYYY-MM-DD.md` | Daily trends report: last night's signals scored against the day's actual outcome. Format template: `reports/2026-07-13.md`. |
| `README-MONITORING.md` | Runbook: exact schedules + prompts for recreating them. Keep in sync with reality — recovery depends on it. |
| `STRATEGY.md` | Trading mandate (aggressive, momentum, stops, floor ratchet). The agent trades against this document. |

## Price data (hard-won lesson)

- **Only trusted source: Yahoo Finance v8 chart API**
  (`query1.finance.yahoo.com/v8/finance/chart/<SYM>?range=1d&interval=5m`,
  User-Agent header required). The container's network allowlist permits
  this host plus E*TRADE's domains; everything else is blocked.
- WebSearch/scraped quotes are BANNED as fill sources — day 1 suffered a
  phantom-price incident (fills at prices that never traded) from mixed
  cached web sources. All fills were later repriced to verified Yahoo 5m
  candles (see `amended_from` fields in the ledger) and one order was
  retroactively rejected for insufficient cash at the real price.
- Paper fill rule: most recent verifiable Yahoo quote, recorded with
  source string; no backdating; regular market hours only.

## Scheduling (survives container restarts)

The cloud session's container is reclaimed when idle (observed ~hourly),
killing in-session cron jobs and background processes. Architecture
therefore splits schedules:

- **Cloud Routines (persistent, fire into the same session):**
  - Overnight signal check — `37 0-12,21-23 * * *` UTC (hourly while US
    markets closed): BTC (MARA is a BTC proxy), futures, position news →
    `overnight-notes.md`.
  - Pre-market briefing — `3 13 * * 1-5` UTC (9:03am ET): writes the day's
    plan, recreates the in-session tick cron, re-arms `watch.py`.
  - Watchdog — `40 13-20 * * 1-5` UTC (market hours): recreates tick cron,
    re-arms watcher, backfills marks older than 30 min.
  - Daily trends report — `20 20 * * 1-5` UTC (4:20pm ET): writes
    `reports/<date>.md`, messages the owner.
- **In-session cron (recreated after every restart by briefing/watchdog):**
  - Market-hours tick — `*/5 13-20 * * 1-5` UTC: mark prices, evaluate
    guardrails + strategy, republish dashboard, commit/push. Fires only
    while the session is idle; conversation preempts it (that's why
    `watch.py` also auto-marks).

All times UTC; ET offset is UTC-4 (EDT). DST note in README-MONITORING.md.

## Invariants (do not break)

1. **Ledger integrity**: never delete or silently edit trades/marks.
   Corrections are additive — `amended_from` + `amendment` fields, done in
   a dedicated commit that explains itself. The experiment's credibility
   is the git history.
2. **Honest simulation**: whole shares, long only, cash never negative,
   no fills outside market hours, no options (option spreads can't be
   simulated honestly on delayed quotes).
3. **Guardrail floor only ratchets up** (STRATEGY.md): stops may tighten,
   never widen. Owner milestone: floor ≥ $120.
4. **Secrets**: consumer keys/secrets live in session env vars only —
   never in code, commits, chat, or this file. `tokens.json`, `.env`
   gitignored. The paper challenge needs NO E*TRADE credentials.
5. **Failures must be loud**: no silent fallbacks that present stale data
   as live (see live_dashboard.py's STALE banner pattern).
6. **Real money is opt-in**: production E*TRADE trading requires the
   owner's explicit go, agreed limits written into STRATEGY.md first, and
   (agreed) an initial preview-and-approve mode.

## Current state (2026-07-14, early UTC)

Paper book: 2 SOFI @ $18.33, 2 MARA @ $12.24, 4 RIG @ $5.28, cash $17.65;
equity ~$99.77; guardrail floor $91.19. Live context: US-Iran Hormuz
blockade effective 4pm ET Jul 14; oil spiking (RIG tailwind), risk-off
tape (SOFI/MARA headwind). Branch: `claude/etrade-api-setup-pid44z`.
