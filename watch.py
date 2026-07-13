"""Fast price watcher for the paper-trading challenge.

Polls quotes for all ledger positions and exits with an alert the moment
any position crosses its guardrail band, so the supervising agent can
react within seconds instead of on the next scheduled check.

    python watch.py --once            # print current quotes and band status
    python watch.py --duration 3300   # poll until breach or timeout

Exit codes: 0 = clean timeout, 2 = guardrail breached (alert on stdout).
Bands live in challenge/guardrails.json; defaults are stop -12% and
target +20% relative to average cost.

Quote source: stooq.com free CSV (delayed ~15 min). The environment's
network allowlist must include stooq.com.
"""

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

LEDGER = Path(__file__).parent / "challenge" / "ledger.json"
GUARDRAILS = Path(__file__).parent / "challenge" / "guardrails.json"
DEFAULT_BANDS = {"stop_pct": -12.0, "target_pct": 20.0}


def load_positions():
    ledger = json.loads(LEDGER.read_text())
    return ledger["positions"]


def load_bands():
    if GUARDRAILS.exists():
        return json.loads(GUARDRAILS.read_text())
    return {}


def fetch_quotes(symbols):
    tickers = ",".join(f"{s.lower()}.us" for s in symbols)
    url = f"https://stooq.com/q/l/?s={tickers}&f=sd2t2ohlcv&h&e=csv"
    with urllib.request.urlopen(url, timeout=15) as resp:
        text = resp.read().decode()
    quotes = {}
    for row in csv.DictReader(io.StringIO(text)):
        close = row.get("Close")
        if close and close != "N/D":
            quotes[row["Symbol"].replace(".US", "").upper()] = float(close)
    return quotes


def check(positions, bands):
    quotes = fetch_quotes(list(positions))
    alerts = []
    status = []
    for sym, pos in positions.items():
        price = quotes.get(sym)
        if price is None:
            status.append({"symbol": sym, "error": "no quote"})
            continue
        band = {**DEFAULT_BANDS, **bands.get(sym, {})}
        move_pct = (price - pos["avg_cost"]) / pos["avg_cost"] * 100
        entry = {"symbol": sym, "price": price, "avg_cost": pos["avg_cost"],
                 "move_pct": round(move_pct, 2), "band": band}
        status.append(entry)
        if move_pct <= band["stop_pct"]:
            alerts.append({**entry, "alert": "STOP"})
        elif move_pct >= band["target_pct"]:
            alerts.append({**entry, "alert": "TARGET"})
    return status, alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--duration", type=int, default=3300, help="seconds")
    parser.add_argument("--interval", type=int, default=30, help="seconds")
    args = parser.parse_args()

    positions = load_positions()
    if not positions:
        print("No positions to watch.")
        return 0
    bands = load_bands()

    deadline = time.time() + args.duration
    while True:
        try:
            status, alerts = check(positions, bands)
        except Exception as e:
            print(f"quote fetch failed: {e}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(min(args.interval * 4, 300))
            continue
        if args.once:
            print(json.dumps(status, indent=2))
            return 0
        if alerts:
            print(json.dumps({"alerts": alerts, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
            return 2
        if time.time() >= deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
