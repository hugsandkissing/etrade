# Challenge monitoring stack — runbook

Architecture for the $100 → $200 paper-trading challenge. Containers are
reclaimed when idle (observed ~hourly overnight), which wipes in-session
cron jobs and background processes — so only the market tick lives
in-session; everything else is a cloud-side CCR Routine that survives
restarts:

1. **Market-hours tick** — in-session cron `*/5 13-19 * * 1-5` (UTC;
   ends 19:55 to avoid post-close no-op firings).
   Recreated by the watchdog or briefing whenever a restart wipes it.
   KNOWN LIMITATION (2026-07-14): in-session cron does not reliably fire
   between turns in this environment — treat it as best-effort only. The
   guaranteed dashboard cadence comes from cloud refresh Routines at :12
   and :26 (hours 14-20 UTC weekdays) plus the :40 watchdog.
2. **Overnight signal check** — CCR Routine, `37 0-12,21-23 * * *` (UTC).
3. **Pre-market briefing** — CCR Routine, `3 13 * * 1-5` (UTC). Also
   recreates the tick cron and re-arms the guardrail watcher each morning.
4. **Cloud watchdog** — CCR Routine, `40 13-20 * * 1-5` (UTC): recreates
   the tick cron, re-arms the watcher process, and backfills stale marks
   during market hours.
5. **Daily trends report** — CCR Routine, `20 20 * * 1-5` (UTC): after the
   close, writes reports/YYYY-MM-DD.md scoring last night's signals against
   the day's actual outcome (format: reports/2026-07-13.md) and messages it
   to the owner.
6. **YUM/Taco Bell outbreak watch** — CCR Routine `55 * * * *` (UTC, 24/7),
   trigger id `trig_01PtGADFsSVokzUYWPg9Vujx`. TEMPORARY, owner-requested:
   compares news/CDC developments and YUM price against
   `challenge/yum-watch.md`, messages the owner immediately on material
   change, silent otherwise. DELETE (delete_trigger) once a YUM move is
   made or the owner says stop. CDC/FDA pages 403 automated fetches —
   sourcing is WebSearch (national + local outlets carry gov updates).

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
> `.venv/bin/python watch.py --duration 25500 --interval 30 --mark-every 300`
> in the background. Message the user only if the plan involves a trade.
