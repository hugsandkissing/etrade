## 2026-07-13 16:22 UTC — DATA INTEGRITY FLAG
Provider quotes for MARA conflict badly today: Robinhood-cached shows $12.65
(range 12.33–13.70); a TradingView-attributed snippet showed $14.49 (range
13.34–15.00) — and $14.49 also matches MARA's June 22 OPEN, so the earlier
SELL 1 MARA @ 14.49 fill may have used stale data. ACTION REQUIRED at next
reliable data point (tonight's close, or stooq once allowlisted): verify
today's true OHLC from 2+ agreeing sources; if 14.49 falls outside today's
real range, amend the sell fill to a defensible in-range price and adjust
cash/equity accordingly. No further trades until prices verify. Trailing
stop on remaining 2 MARA (+15% = 13.18) is also suspended pending
verification — do not sell on unverifiable quotes.

## 2026-07-13 16:35 UTC — RESOLVED: ledger reconciled against Yahoo feed
Owner allowlisted query1.finance.yahoo.com. Verified today's real ranges
(MARA 12.055-12.65 — both the 11.46 entry and 14.49 sell were phantom
prices from stale providers). All five fills repriced to the Yahoo 5m
candle at each trade's timestamp; the 16:08 BUY 1 SOFI was REJECTED on
replay (insufficient cash at real prices). True equity: $99.13 (-0.9%).
Old marks flagged unverified_source. watch.py switched to Yahoo. Trade
suspension LIFTED; guardrails reset to strategy defaults vs corrected
costs. WebSearch is no longer an acceptable fill source — Yahoo feed only.
