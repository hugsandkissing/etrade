"""Dump a day's challenge data as JSON for the daily trends report.

    python report_data.py [YYYY-MM-DD]   # default: today (UTC)

Pulls from challenge/ledger.json: that day's equity path (from marks),
trades with rationale, and per-symbol price moves between the day's first
and last marks.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(__file__).parent / "challenge" / "ledger.json"


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ledger = json.loads(LEDGER.read_text())

    marks = [m for m in ledger["marks"] if m["ts"].startswith(day)]
    trades = [t for t in ledger["trades"] if t["ts"].startswith(day)]

    symbols = sorted({s for m in marks for s in m["prices"]})
    moves = {}
    for sym in symbols:
        series = [m["prices"][sym] for m in marks if sym in m["prices"]]
        if series:
            moves[sym] = {
                "first": series[0], "last": series[-1],
                "low": min(series), "high": max(series),
                "change_pct": round((series[-1] - series[0]) / series[0] * 100, 2),
            }

    equities = [m["equity"] for m in marks]
    print(json.dumps({
        "day": day,
        "marks": len(marks),
        "equity": {
            "first": equities[0] if equities else None,
            "last": equities[-1] if equities else None,
            "low": min(equities) if equities else None,
            "high": max(equities) if equities else None,
        },
        "positions_now": ledger["positions"],
        "cash": ledger["cash"],
        "symbol_moves": moves,
        "trades": trades,
    }, indent=2))


if __name__ == "__main__":
    main()
