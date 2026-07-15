"""Paper-trading ledger for the $100 -> $200 challenge.

All trades are simulated. Prices are supplied by the operator from a real
quote source (recorded alongside each trade for auditability). The ledger
lives in challenge/ledger.json and is committed to git so the full history
is verifiable.

    python challenge.py init
    python challenge.py buy SOFI 3 24.85 --source "stooq 2026-07-13 14:02 UTC" --note "why"
    python challenge.py sell SOFI 3 26.10 --source "..." --note "why"
    python challenge.py mark SOFI=25.10 MARA=18.22   # valuation snapshot
    python challenge.py status

Rules enforced: long only, cash can never go negative. Fractional shares
allowed (min 0.001 sh, 3 decimal places; buys need $5+ notional) —
mirroring E*TRADE's real fractional terms, enabled by the owner 2026-07-15.
"""

import argparse
import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(__file__).parent / "challenge" / "ledger.json"
START_CASH = 100.0
GOAL = 200.0


@contextmanager
def locked():
    """Serialize ledger writes across processes (session ticks + watcher)."""
    LEDGER.parent.mkdir(exist_ok=True)
    with open(LEDGER.parent / ".ledger.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load():
    if not LEDGER.exists():
        raise SystemExit("No ledger. Run: python challenge.py init")
    return json.loads(LEDGER.read_text())


def save(ledger):
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")


def equity(ledger, prices=None):
    """Cash + positions valued at `prices`, falling back to the last mark."""
    prices = prices or (ledger["marks"][-1]["prices"] if ledger["marks"] else {})
    total = ledger["cash"]
    for sym, pos in ledger["positions"].items():
        total += pos["qty"] * prices.get(sym, pos["avg_cost"])
    return total


def cmd_init(args):
    if LEDGER.exists() and not args.force:
        raise SystemExit("Ledger already exists (use --force to reset).")
    save({
        "start_cash": START_CASH, "goal": GOAL, "created": now(),
        "cash": START_CASH, "positions": {}, "trades": [], "marks": [],
    })
    print(f"Challenge started: ${START_CASH:.2f} -> ${GOAL:.2f}")


def cmd_trade(args, action):
    with locked():
        _do_trade(args, action)


def _do_trade(args, action):
    ledger = load()
    sym = args.symbol.upper()
    qty, price = round(args.quantity, 3), args.price
    if qty < 0.001:
        raise SystemExit("Quantity must be at least 0.001 shares.")
    if qty != args.quantity:
        raise SystemExit("Quantity precision is 3 decimal places (E*TRADE fractional terms).")
    cost = qty * price

    if action == "buy":
        if cost < 5.0 - 1e-9:
            raise SystemExit(f"Buy notional ${cost:.2f} below the $5 minimum "
                             "(E*TRADE fractional terms).")
        if cost > ledger["cash"] + 1e-9:
            raise SystemExit(f"Insufficient cash: need ${cost:.2f}, have ${ledger['cash']:.2f}")
        ledger["cash"] -= cost
        pos = ledger["positions"].setdefault(sym, {"qty": 0, "avg_cost": 0.0})
        pos["avg_cost"] = (pos["avg_cost"] * pos["qty"] + cost) / (pos["qty"] + qty)
        pos["qty"] = round(pos["qty"] + qty, 3)
    else:
        pos = ledger["positions"].get(sym)
        if not pos or pos["qty"] + 1e-9 < qty:
            held = pos["qty"] if pos else 0
            raise SystemExit(f"Cannot sell {qty:g} {sym}: hold {held:g}")
        ledger["cash"] += cost
        pos["qty"] = round(pos["qty"] - qty, 3)
        if pos["qty"] < 0.001:
            del ledger["positions"][sym]

    ledger["trades"].append({
        "ts": now(), "action": action.upper(), "symbol": sym,
        "qty": qty, "price": price, "source": args.source, "note": args.note,
    })
    save(ledger)
    print(f"{action.upper()} {qty:g} {sym} @ ${price:.2f} — cash ${ledger['cash']:.2f}")


def record_mark(prices, source=""):
    """Append a valuation snapshot. Safe to call from any process."""
    with locked():
        ledger = load()
        missing = set(ledger["positions"]) - set(prices)
        if missing:
            raise SystemExit(f"Missing prices for held positions: {', '.join(sorted(missing))}")
        eq = equity(ledger, prices)
        ledger["marks"].append({"ts": now(), "prices": prices, "equity": round(eq, 2),
                                "source": source})
        save(ledger)
    return eq


def cmd_mark(args):
    prices = {}
    for pair in args.prices:
        sym, _, price = pair.partition("=")
        prices[sym.upper()] = float(price)
    eq = record_mark(prices, args.source)
    print(f"Marked: equity ${eq:.2f} ({(eq - START_CASH) / START_CASH * +100:+.1f}%)")


def cmd_status(args):
    ledger = load()
    eq = equity(ledger)
    print(f"Equity ${eq:.2f} / goal ${ledger['goal']:.2f} "
          f"({(eq - ledger['start_cash']) / ledger['start_cash'] * 100:+.1f}%) — "
          f"cash ${ledger['cash']:.2f}")
    last = ledger["marks"][-1]["prices"] if ledger["marks"] else {}
    for sym, pos in sorted(ledger["positions"].items()):
        px = last.get(sym, pos["avg_cost"])
        val = pos["qty"] * px
        gain = (px - pos["avg_cost"]) * pos["qty"]
        print(f"  {sym}: {pos['qty']:g} @ avg ${pos['avg_cost']:.2f}, "
              f"last ${px:.2f}, value ${val:.2f}, gain {gain:+.2f}")
    print(f"Trades: {len(ledger['trades'])}, marks: {len(ledger['marks'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--force", action="store_true")
    for name in ("buy", "sell"):
        p = sub.add_parser(name)
        p.add_argument("symbol"); p.add_argument("quantity", type=float)
        p.add_argument("price", type=float)
        p.add_argument("--source", required=True, help="where the price came from")
        p.add_argument("--note", default="", help="trade rationale")
    p = sub.add_parser("mark")
    p.add_argument("prices", nargs="+", metavar="SYM=PRICE")
    p.add_argument("--source", default="")
    sub.add_parser("status")

    args = parser.parse_args()
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd in ("buy", "sell"):
        cmd_trade(args, args.cmd)
    elif args.cmd == "mark":
        cmd_mark(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()
