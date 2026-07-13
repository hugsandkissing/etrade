"""Smoke test: list accounts and fetch a quote.

Run auth.py first to obtain tokens, then:

    python example.py [SYMBOL]
"""

import json
import sys

import etrade_client as ec


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    session, base = ec.get_session()

    resp = session.get(f"{base}/v1/accounts/list.json")
    resp.raise_for_status()
    print("Accounts:")
    print(json.dumps(resp.json(), indent=2))

    resp = session.get(f"{base}/v1/market/quote/{symbol}.json")
    resp.raise_for_status()
    print(f"\nQuote for {symbol}:")
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    main()
