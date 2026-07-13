# Challenge strategy — aggressive mandate

Owner directive (2026-07-13): reach $200 as fast as possible; no deadline.
Accept high variance. This file governs trade decisions made by the
monitoring loop; update it only on owner instruction.

## Posture

- Stay ~fully deployed; idle cash is drag. Concentrate in 2–3 positions max.
- Momentum first: rotate into whatever is moving with volume and a catalyst;
  don't marry a thesis. A position that goes nowhere for ~2 weeks is dead
  capital — rotate it.
- Cut fast, ride winners: stops at -10% (guardrails.json), but raise the stop
  toward breakeven once a position is +10%, and trail it under winners rather
  than taking profit at a fixed target.
- Redeploy sale proceeds the same session; the challenge compounds or it dies.

## Instruments allowed (paper)

- US-listed stocks and ETFs, whole shares only, long only.
- Leveraged/inverse ETFs explicitly allowed (2x/3x, e.g. TSLL, BITX, SOXS):
  this is the sanctioned leverage and the bear-side tool, since we cannot
  short. Prefer per-share price under ~$50 so the account can size them.
  Respect decay: these are days-to-weeks holds, never buy-and-forget.
- Crypto exposure via ETFs (IBIT/BITX) or miners (MARA), not direct coins.
- NOT allowed: options (paper fills on wide option spreads would flatter
  results — revisit only with a real-time quote source), futures, forex,
  fractional shares, margin. Score honestly or the experiment is worthless.

## Floor ratchet (owner goal, 2026-07-13)

The guardrail floor = cash + every position valued at its stop price. It
only moves UP: as equity grows, trail stops so the floor ratchets higher,
and never widen a stop once raised. Milestone 1: floor ≥ $120 (requires
equity ≈ $133+ with 10% trails). Balance to respect: trails tighter than
~8-10% on these names get shaken out by ordinary noise — earn the floor
with growth, don't fake it with hair-trigger stops.

## Fill discipline

Paper fills use the most recent verifiable quote, recorded with source in the
ledger. No back-dating, no filling at prices between marks, no trading
outside regular market hours.
