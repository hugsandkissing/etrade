"""Real-time local dashboard for the paper-trading challenge.

Run on your own machine (stdlib only, no pip installs):

    python3 live_dashboard.py          # then open http://localhost:8741

Serves a page that re-prices the current book against Yahoo Finance every
10 seconds. Positions/cash come from challenge/ledger.json at every poll,
so a `git pull` picks up new trades without restarting.

This is the live *pricing* view. Trade decisions still happen in the
Claude session on its own cadence; the artifact dashboard remains the
decision log.
"""

import json
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 8741
LEDGER = Path(__file__).parent / "challenge" / "ledger.json"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Challenge — Live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --ring:rgba(11,11,11,.1);
    --pos:#006300; --neg:#d03b3b; }
  @media (prefers-color-scheme: dark) { :root { --surface:#1a1a19; --page:#0d0d0d;
    --ink:#fff; --ink2:#c3c2b7; --grid:#2c2c2a; --ring:rgba(255,255,255,.1);
    --pos:#0ca30c; --neg:#e66767; } }
  body { font-family: system-ui, sans-serif; background: var(--page);
    color: var(--ink); margin: 0; padding: 24px; }
  .wrap { max-width: 560px; margin: 0 auto; }
  h1 { font-size: 18px; margin: 0 0 2px; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
  .card { background: var(--surface); border: 1px solid var(--ring);
    border-radius: 10px; padding: 16px; margin-bottom: 12px; }
  .equity { font-size: 34px; font-weight: 650; }
  .equity small { font-size: 16px; }
  .pos { color: var(--pos); } .neg { color: var(--neg); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: right; color: var(--ink2); font-size: 11px;
    border-bottom: 1px solid var(--grid); padding: 4px 8px; }
  th:first-child, td:first-child { text-align: left; }
  td { text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--grid);
    font-variant-numeric: tabular-nums; }
  .stale { color: var(--neg); font-weight: 600; }
</style></head><body><div class="wrap">
<h1>$100 &rarr; $200 Challenge — live pricing</h1>
<div class="sub" id="status">connecting&hellip;</div>
<div class="card"><div style="font-size:12px;color:var(--ink2)">Equity</div>
  <div class="equity" id="equity">—</div>
  <div style="font-size:12px;color:var(--ink2)" id="cash"></div></div>
<div class="card"><table>
  <thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Value</th><th>P&amp;L</th></tr></thead>
  <tbody id="rows"></tbody></table></div>
<div class="sub">Quotes: Yahoo Finance, polled every 10s. Book state: challenge/ledger.json
 (git pull to refresh trades). Market-closed quotes show the last trade.</div>
</div>
<script>
const usd = v => (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
const sgn = v => (v >= 0 ? "+" : "") + usd(v).replace("$-", "-$");
async function tick() {
  try {
    const r = await (await fetch("/quotes")).json();
    let rows = "", value = r.cash;
    for (const [sym, p] of Object.entries(r.positions)) {
      const last = r.prices[sym], val = p.qty * last, pnl = (last - p.avg_cost) * p.qty;
      value += val;
      rows += `<tr><td>${sym}</td><td>${p.qty}</td><td>${usd(p.avg_cost)}</td>
        <td>${usd(last)}</td><td>${usd(val)}</td>
        <td class="${pnl < 0 ? "neg" : "pos"}">${sgn(pnl)}</td></tr>`;
    }
    document.getElementById("rows").innerHTML = rows;
    const pct = (value - r.start_cash) / r.start_cash * 100;
    const eq = document.getElementById("equity");
    eq.innerHTML = usd(value) + ` <small>(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</small>`;
    eq.className = "equity " + (value < r.start_cash ? "neg" : "pos");
    document.getElementById("cash").textContent = "Cash " + usd(r.cash) +
      " · goal $" + r.goal.toFixed(0);
    document.getElementById("status").textContent =
      "live · updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("status").innerHTML =
      '<span class="stale">disconnected — is live_dashboard.py running?</span>';
  }
}
tick(); setInterval(tick, 10000);
</script></body></html>"""


def yahoo_price(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["chart"]["result"][0]["meta"]["regularMarketPrice"]


class Handler(BaseHTTPRequestHandler):
    _cache = {}

    def log_message(self, *args):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path == "/quotes":
            ledger = json.loads(LEDGER.read_text())
            prices = {}
            for sym in ledger["positions"]:
                cached = self._cache.get(sym)
                if cached and time.time() - cached[1] < 8:
                    prices[sym] = cached[0]
                else:
                    try:
                        prices[sym] = yahoo_price(sym)
                        self._cache[sym] = (prices[sym], time.time())
                    except Exception:
                        # fall back to last ledger mark if Yahoo hiccups
                        marks = ledger.get("marks", [])
                        prices[sym] = (marks[-1]["prices"].get(sym)
                                       if marks else ledger["positions"][sym]["avg_cost"])
            self._send(json.dumps({
                "prices": prices, "positions": ledger["positions"],
                "cash": ledger["cash"], "start_cash": ledger["start_cash"],
                "goal": ledger["goal"],
            }), "application/json")
        else:
            self._send(PAGE, "text/html")


if __name__ == "__main__":
    print(f"Live dashboard: http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
