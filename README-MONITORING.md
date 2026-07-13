# Challenge monitoring stack — runbook

Architecture for the $100 → $200 paper-trading challenge. Four layers:

1. **Market-hours tick** — in-session cron `*/5 13-20 * * 1-5` (UTC).
2. **Overnight signal check** — in-session cron `37 0-12,21-23 * * *` (UTC).
3. **Pre-market briefing** — in-session cron `3 13 * * 1-5` (UTC).
4. **Cloud watchdog** — CCR Routine, hourly at :40, revives layers 1–3 if the
   session restarted or crons expired (they auto-expire after 7 days).

Guardrail bands per position live in `challenge/guardrails.json`; `watch.py`
polls them every 30s when stooq.com is network-allowlisted. Overnight notes
accumulate in `challenge/overnight-notes.md`. All ledger changes commit to
`claude/etrade-api-setup-pid44z`.

DST note: cron hours above assume US Eastern DAYLIGHT time (EDT, UTC-4).
When clocks fall back (early November), shift the market-hours tick to
`*/5 14-21 * * 1-5` and the briefing to `3 14 * * 1-5`.

## Exact prompts for recreating the in-session crons

### Layer 1 — market-hours tick (`*/5 13-20 * * 1-5`)

> Challenge market-hours tick (only act 9:30am-4pm ET; if outside that window
> or a US market holiday, do nothing). Get current prices for all positions in
> challenge/ledger.json (try `python watch.py --once` first; fall back to
> WebSearch). If prices changed: record a mark via challenge.py, evaluate
> guardrails in challenge/guardrails.json and the strategy (trade only with
> logged rationale in --note; most ticks are hold), regenerate
> `python dashboard.py`, republish the artifact at the existing URL, commit
> and push the ledger. Stay silent unless a trade executed or equity crossed
> $120 / fell below $80.

### Layer 2 — overnight signal check (`37 0-12,21-23 * * *`)

> Challenge overnight signal check (runs when US equities are closed; skip
> instantly if somehow within market hours). Use WebSearch to check:
> 1) Bitcoin price and its move since our last overnight note — MARA is a BTC
> proxy, so a move over ±5% means I should draft a MARA action plan for the
> next open; 2) S&P/Nasdaq futures direction if it is a weeknight; 3) any
> breaking news on SOFI, MARA, or RIG. Append a dated bullet summary to
> challenge/overnight-notes.md (create if missing), commit and push only if
> you wrote a note worth keeping (skip routine 'nothing happened' commits).
> No trades — the paper challenge only fills during market hours. Message the
> user only for a >±8% BTC move or major position news.

### Layer 3 — pre-market briefing (`3 13 * * 1-5`)

> Challenge pre-market briefing (9:03am ET weekdays; skip on US market
> holidays). Use WebSearch for: pre-market quotes/gaps on SOFI, MARA, RIG;
> overnight BTC move; index futures; any dated news on the three names. Read
> challenge/overnight-notes.md for context. Decide the plan for today's open —
> hold, exit at open, or reallocate — and write it as a dated entry in
> challenge/overnight-notes.md with reasoning, commit and push. If the plan
> includes trades, execute them via challenge.py shortly after 9:35am ET at
> real opening prices (the market-hours tick will handle it if you note the
> plan clearly). Also re-arm the guardrail watcher for the session: run
> `.venv/bin/python watch.py --duration 24000 --interval 30 --mark-every 300`
> in the background. Message the user only if the plan involves a trade.
