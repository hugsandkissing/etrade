"""Generate a self-contained HTML portfolio dashboard from E*TRADE data.

    python dashboard.py [output.html]

Fetches accounts, balances, and positions, then writes a static HTML file
(default dashboard.html, gitignored) with stat tiles, allocation and
day's-gain charts, and a positions table. Sandbox note: E*TRADE's sandbox
returns the same canned portfolio for every account, so identical
portfolios are deduplicated.
"""

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import etrade_client as ec

CHALLENGE_LEDGER = Path(__file__).parent / "challenge" / "ledger.json"
GUARDRAILS_FILE = Path(__file__).parent / "challenge" / "guardrails.json"


def guardrail_floor(ledger):
    """Worst-case equity if every position sold at its stop price."""
    try:
        bands = json.loads(GUARDRAILS_FILE.read_text())
    except OSError:
        return None
    default = bands.get("_default", {"stop_pct": -10.0})
    floor = ledger["cash"]
    for sym, pos in ledger["positions"].items():
        stop_pct = bands.get(sym, default)["stop_pct"]
        floor += pos["qty"] * pos["avg_cost"] * (1 + stop_pct / 100)
    return floor


def fetch_portfolio():
    key, secret, sandbox = ec.load_config()
    session, base = ec.get_session()
    accounts = session.get(f"{base}/v1/accounts/list.json").json()[
        "AccountListResponse"]["Accounts"]["Account"]
    if isinstance(accounts, dict):
        accounts = [accounts]

    seen = set()
    positions = []
    account_rows = []
    for acct in accounts:
        if acct.get("accountStatus") != "ACTIVE":
            continue
        resp = session.get(f"{base}/v1/accounts/{acct['accountIdKey']}/portfolio.json")
        if not resp.ok:
            continue
        for ap in resp.json()["PortfolioResponse"]["AccountPortfolio"]:
            # Sandbox serves one canned portfolio under every account
            if ap["accountId"] in seen:
                continue
            seen.add(ap["accountId"])
            account_rows.append(acct)
            for pos in ap.get("Position", []):
                quick = pos.get("Quick", {})
                positions.append({
                    "symbol": pos["Product"]["symbol"],
                    "quantity": pos["quantity"],
                    "lastTrade": quick.get("lastTrade", 0),
                    "changePct": quick.get("changePct", 0),
                    "marketValue": pos.get("marketValue", 0),
                    "daysGain": pos.get("daysGain", 0),
                    "totalGain": pos.get("totalGain", 0),
                    "pricePaid": pos.get("pricePaid", 0),
                })
    return sandbox, account_rows, positions


def fmt_usd(v):
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def fmt_signed_usd(v):
    return ("+" if v > 0 else "") + fmt_usd(v)


def diverging_bars(rows, value_key, detail):
    """Horizontal diverging bar rows: negatives left of a shared zero line."""
    values = [r[value_key] for r in rows]
    lo, hi = min(min(values), 0), max(max(values), 0)
    span = (hi - lo) or 1
    zero_pct = -lo / span * 100
    out = []
    for r in sorted(rows, key=lambda r: -r[value_key]):
        v = r[value_key]
        width = abs(v) / span * 100
        left = zero_pct - width if v < 0 else zero_pct
        cls = "neg" if v < 0 else "pos"
        out.append(
            f'<div class="bar-row" data-tip="{html.escape(detail(r))}">'
            f'<span class="bar-sym">{html.escape(r["symbol"])}</span>'
            f'<span class="bar-track"><span class="bar-zero" style="left:{zero_pct:.2f}%"></span>'
            f'<span class="bar-fill {cls}" style="left:{left:.2f}%;width:{max(width, 0.5):.2f}%"></span></span>'
            f'<span class="bar-val {cls}">{fmt_signed_usd(v)}</span></div>'
        )
    return "\n".join(out)


def challenge_section():
    """Render the $100 -> $200 paper-trading challenge panel, if a ledger exists."""
    if not CHALLENGE_LEDGER.exists():
        return ""
    ledger = json.loads(CHALLENGE_LEDGER.read_text())
    marks = ledger["marks"]
    prices = marks[-1]["prices"] if marks else {}
    equity = ledger["cash"] + sum(
        pos["qty"] * prices.get(sym, pos["avg_cost"])
        for sym, pos in ledger["positions"].items())
    start, goal = ledger["start_cash"], ledger["goal"]
    pct = (equity - start) / start * 100
    eq_cls = "neg" if equity < start else "pos"
    bar_pct = max(0, min(equity / goal * 100, 100))
    start_pct = start / goal * 100

    spark = ""
    if len(marks) >= 2:
        eqs = [m["equity"] for m in marks]
        lo, hi = min(eqs + [start]), max(eqs + [start])
        span = (hi - lo) or 1
        n = len(eqs)
        pts = " ".join(f"{i / (n - 1) * 100:.2f},{30 - (e - lo) / span * 26:.2f}"
                       for i, e in enumerate(eqs))
        base_y = 30 - (start - lo) / span * 26
        end_x, end_y = 100, 30 - (eqs[-1] - lo) / span * 26
        spark = (f'<svg viewBox="0 0 100 32" preserveAspectRatio="none" class="spark">'
                 f'<line x1="0" y1="{base_y:.2f}" x2="100" y2="{base_y:.2f}" class="spark-base"/>'
                 f'<polyline points="{pts}" class="spark-line"/>'
                 f'<circle cx="{end_x}" cy="{end_y:.2f}" r="1.6" class="spark-dot"/></svg>')

    floor = guardrail_floor(ledger)
    floor_html = ""
    if floor is not None:
        floor_html = (f' · Guardrail floor: {fmt_usd(floor)}'
                      f' <span style="color:var(--ink-muted)">(milestone: ≥ $120)</span>')

    pos_rows = "\n".join(
        f"<tr><td>{html.escape(sym)}</td><td class='num'>{pos['qty']}</td>"
        f"<td class='num'>{fmt_usd(pos['avg_cost'])}</td>"
        f"<td class='num'>{fmt_usd(prices.get(sym, pos['avg_cost']))}</td>"
        f"<td class='num'>{fmt_usd(pos['qty'] * prices.get(sym, pos['avg_cost']))}</td>"
        f"<td class='num {'neg' if prices.get(sym, pos['avg_cost']) < pos['avg_cost'] else 'pos'}'>"
        f"{fmt_signed_usd((prices.get(sym, pos['avg_cost']) - pos['avg_cost']) * pos['qty'])}</td></tr>"
        for sym, pos in sorted(ledger["positions"].items()))

    trade_items = "\n".join(
        f"<li><strong>{t['action']} {t['qty']} {html.escape(t['symbol'])}</strong> @ "
        f"{fmt_usd(t['price'])} <span class='muted'>({t['ts'][:10]})</span>"
        + (f"<br><span class='muted'>{html.escape(t['note'])}</span>" if t.get("note") else "")
        + "</li>"
        for t in reversed(ledger["trades"][-5:]))

    return f"""
<div class="card" style="margin-bottom:16px">
  <h2>$100 &rarr; $200 Challenge <span class="badge">PAPER — no real money</span></h2>
  <p class="hint">Started {ledger['created'][:10]} · {len(ledger['trades'])} trades ·
    scored against real market prices</p>
  <div class="ch-grid">
    <div>
      <div class="label">Equity</div>
      <div class="value {eq_cls}" style="font-size:28px;font-weight:600">{fmt_usd(equity)}
        <span style="font-size:14px">({pct:+.1f}%)</span></div>
      <div class="progress"><span class="progress-fill" style="width:{bar_pct:.1f}%"></span>
        <span class="progress-start" style="left:{start_pct:.0f}%"></span></div>
      <div class="label" style="display:flex;justify-content:space-between">
        <span>$0</span><span>start $100</span><span>goal $200</span></div>
      {spark}
      <div class="label" style="margin-top:8px">Cash: {fmt_usd(ledger['cash'])}{floor_html}</div>
    </div>
    <div>
      <table>
        <thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Avg cost</th>
          <th class="num">Last</th><th class="num">Value</th><th class="num">Gain</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
      <div class="label" style="margin:10px 0 4px">Recent trades</div>
      <ul class="trades">{trade_items}</ul>
    </div>
  </div>
</div>"""


def build_html(sandbox, accounts, positions):
    challenge = challenge_section()
    if not positions:
        etrade_html = ('<div class="card"><h2>E*TRADE account</h2>'
                       '<p class="hint">Account data unavailable — the daily OAuth token '
                       'has likely expired. Run <code>python auth.py</code>.</p></div>')
        return page_template(sandbox, accounts, challenge, etrade_html)

    total_mv = sum(p["marketValue"] for p in positions)
    days_gain = sum(p["daysGain"] for p in positions)
    total_gain = sum(p["totalGain"] for p in positions)
    longs = sum(1 for p in positions if p["quantity"] > 0)
    shorts = len(positions) - longs

    def pos_detail(p):
        kind = "long" if p["quantity"] > 0 else "short"
        return (f"{p['symbol']} · {abs(p['quantity'])} sh {kind} · "
                f"last {fmt_usd(p['lastTrade'])} · day {p['changePct']:+.2f}%")

    mv_bars = diverging_bars(positions, "marketValue", pos_detail)
    gain_bars = diverging_bars(positions, "daysGain", pos_detail)

    table_rows = "\n".join(
        f"<tr><td>{html.escape(p['symbol'])}</td>"
        f"<td class='num'>{p['quantity']}</td>"
        f"<td class='num'>{fmt_usd(p['lastTrade'])}</td>"
        f"<td class='num {'neg' if p['changePct'] < 0 else 'pos'}'>{p['changePct']:+.2f}%</td>"
        f"<td class='num'>{fmt_usd(p['marketValue'])}</td>"
        f"<td class='num {'neg' if p['daysGain'] < 0 else 'pos'}'>{fmt_signed_usd(p['daysGain'])}</td>"
        f"<td class='num {'neg' if p['totalGain'] < 0 else 'pos'}'>{fmt_signed_usd(p['totalGain'])}</td></tr>"
        for p in sorted(positions, key=lambda p: -abs(p["marketValue"]))
    )

    gain_cls = "neg" if days_gain < 0 else "pos"
    tgain_cls = "neg" if total_gain < 0 else "pos"

    etrade_html = f"""<div class="tiles">
  <div class="tile"><div class="label">Net market value</div><div class="value">{fmt_usd(total_mv)}</div></div>
  <div class="tile"><div class="label">Day's gain</div><div class="value {gain_cls}">{fmt_signed_usd(days_gain)}</div></div>
  <div class="tile"><div class="label">Total gain</div><div class="value {tgain_cls}">{fmt_signed_usd(total_gain)}</div></div>
  <div class="tile"><div class="label">Positions</div><div class="value">{len(positions)}</div>
    <div class="label" style="margin:4px 0 0">{longs} long · {shorts} short</div></div>
</div>
<div class="cards">
  <div class="card"><h2>Market value by position</h2>
    <p class="hint">Short positions extend left of the zero line</p>{mv_bars}</div>
  <div class="card"><h2>Day's gain by position</h2>
    <p class="hint">Today's dollar change per position</p>{gain_bars}</div>
</div>
<div class="card table-scroll">
  <h2>Positions</h2>
  <table>
    <thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Last</th>
      <th class="num">Day %</th><th class="num">Mkt value</th><th class="num">Day gain</th>
      <th class="num">Total gain</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</div>"""
    return page_template(sandbox, accounts, challenge, etrade_html)


def page_template(sandbox, accounts, challenge, etrade_html):
    acct_names = ", ".join(
        f"{a.get('accountDesc') or a.get('accountType')} ({a['accountId']})"
        for a in accounts) or "account data unavailable"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    env_badge = "SANDBOX — simulated data" if sandbox else "PRODUCTION"

    return f"""<title>Portfolio Dashboard</title>
<style>
  .viz-root {{
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --ink-1: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --ring: rgba(11,11,11,0.10);
    --pos: #2a78d6; --neg: #e34948;
    --pos-text: #006300; --neg-text: #d03b3b;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page); color: var(--ink-1);
    min-height: 100vh; padding: 24px; box-sizing: border-box;
  }}
  @media (prefers-color-scheme: dark) {{ .viz-root {{
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --ring: rgba(255,255,255,0.10);
    --pos: #3987e5; --neg: #e66767;
    --pos-text: #0ca30c; --neg-text: #e66767;
  }} }}
  :root[data-theme="dark"] .viz-root {{
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink-1: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --ring: rgba(255,255,255,0.10);
    --pos: #3987e5; --neg: #e66767;
    --pos-text: #0ca30c; --neg-text: #e66767;
  }}
  :root[data-theme="light"] .viz-root {{
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --ink-1: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --ring: rgba(11,11,11,0.10);
    --pos: #2a78d6; --neg: #e34948;
    --pos-text: #006300; --neg-text: #d03b3b;
  }}
  .viz-root * {{ box-sizing: border-box; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  header h1 {{ font-size: 20px; margin: 0 0 4px; }}
  header .sub {{ color: var(--ink-2); font-size: 13px; margin: 0 0 20px; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 600; letter-spacing: .04em;
    padding: 2px 8px; border-radius: 10px; border: 1px solid var(--ring);
    color: var(--ink-2); margin-left: 8px; vertical-align: 1px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 16px; }}
  .tile {{ background: var(--surface-1); border: 1px solid var(--ring); border-radius: 10px;
    padding: 14px 16px; }}
  .tile .label {{ font-size: 12px; color: var(--ink-2); margin-bottom: 6px; }}
  .tile .value {{ font-size: 24px; font-weight: 600; }}
  .tile .value.pos {{ color: var(--pos-text); }}
  .tile .value.neg {{ color: var(--neg-text); }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 12px; margin-bottom: 16px; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--ring); border-radius: 10px;
    padding: 16px; }}
  .card h2 {{ font-size: 14px; margin: 0 0 2px; }}
  .card .hint {{ font-size: 12px; color: var(--ink-muted); margin: 0 0 12px; }}
  .bar-row {{ display: grid; grid-template-columns: 52px 1fr 92px; align-items: center;
    gap: 8px; padding: 3px 0; }}
  .bar-sym {{ font-size: 12px; color: var(--ink-2); }}
  .bar-track {{ position: relative; height: 20px; }}
  .bar-zero {{ position: absolute; top: -2px; bottom: -2px; width: 1px;
    background: var(--baseline); }}
  .bar-fill {{ position: absolute; top: 0; height: 20px; border-radius: 4px; }}
  .bar-fill.pos {{ background: var(--pos); }}
  .bar-fill.neg {{ background: var(--neg); }}
  .bar-val {{ font-size: 12px; text-align: right; font-variant-numeric: tabular-nums;
    color: var(--ink-2); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-weight: 600; color: var(--ink-2); font-size: 12px;
    border-bottom: 1px solid var(--baseline); padding: 6px 8px; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid var(--grid); }}
  th.num, td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.pos {{ color: var(--pos-text); }}
  td.neg {{ color: var(--neg-text); }}
  .table-scroll {{ overflow-x: auto; }}
  footer {{ color: var(--ink-muted); font-size: 12px; margin-top: 16px; }}
  .label {{ font-size: 12px; color: var(--ink-2); }}
  .muted {{ color: var(--ink-muted); font-size: 12px; }}
  .value.pos {{ color: var(--pos-text); }}
  .value.neg {{ color: var(--neg-text); }}
  .ch-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 20px; margin-top: 8px; }}
  .progress {{ position: relative; height: 10px; background: var(--grid);
    border-radius: 5px; margin: 10px 0 4px; overflow: hidden; }}
  .progress-fill {{ position: absolute; left: 0; top: 0; bottom: 0;
    background: var(--pos); border-radius: 5px; }}
  .progress-start {{ position: absolute; top: -2px; bottom: -2px; width: 2px;
    background: var(--baseline); }}
  .spark {{ width: 100%; height: 48px; margin-top: 12px; }}
  .spark-line {{ fill: none; stroke: var(--pos); stroke-width: 1.2;
    vector-effect: non-scaling-stroke; }}
  .spark-base {{ stroke: var(--grid); stroke-width: 1; vector-effect: non-scaling-stroke; }}
  .spark-dot {{ fill: var(--pos); }}
  ul.trades {{ margin: 0; padding-left: 18px; font-size: 12px; }}
  ul.trades li {{ margin-bottom: 6px; }}
  #tip {{ position: fixed; pointer-events: none; background: var(--surface-1);
    border: 1px solid var(--ring); border-radius: 8px; padding: 6px 10px; font-size: 12px;
    color: var(--ink-1); box-shadow: 0 2px 8px rgba(0,0,0,.15); display: none; z-index: 10; }}
</style>
<div class="viz-root"><div class="wrap">
<header>
  <h1>Portfolio Dashboard<span class="badge">{env_badge}</span></h1>
  <p class="sub">{html.escape(acct_names)} · generated {stamp}</p>
</header>
{challenge}
{etrade_html}
<footer>Data from the E*TRADE API · page reloads every minute; data marks
~every 5 min in market hours · regenerate with <code>python dashboard.py</code></footer>
</div><div id="tip"></div></div>
<script>
  setTimeout(() => location.reload(), 60000);
  const tip = document.getElementById('tip');
  document.querySelectorAll('.bar-row').forEach(row => {{
    row.addEventListener('mousemove', e => {{
      tip.textContent = row.dataset.tip;
      tip.style.display = 'block';
      const pad = 12, w = tip.offsetWidth;
      tip.style.left = Math.min(e.clientX + pad, innerWidth - w - pad) + 'px';
      tip.style.top = (e.clientY + pad) + 'px';
    }});
    row.addEventListener('mouseleave', () => tip.style.display = 'none');
  }});
</script>
"""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "dashboard.html"
    try:
        sandbox, accounts, positions = fetch_portfolio()
    except Exception as e:
        # Challenge panel still renders when the E*TRADE token has expired
        print(f"E*TRADE fetch failed ({e}); rendering challenge data only")
        sandbox, accounts, positions = True, [], []
    with open(out, "w") as f:
        f.write(build_html(sandbox, accounts, positions))
    print(f"Wrote {out}: {len(positions)} positions across {len(accounts)} account(s)")


if __name__ == "__main__":
    main()
