"""Preview and place equity orders.

E*TRADE requires a two-step flow: every order must be previewed first,
then placed with the preview ID. Examples:

    python orders.py BUY AAPL 10                 # market order
    python orders.py BUY AAPL 10 --limit 150.00  # limit order
    python orders.py SELL AAPL 10 --preview-only # preview, don't place

Sandbox keys operate on fake data — safe to experiment. With a production
key this places real trades; the script makes you confirm before placing.
"""

import argparse
import json
import uuid

import etrade_client as ec


def first_account(session, base):
    resp = session.get(f"{base}/v1/accounts/list.json")
    resp.raise_for_status()
    accounts = resp.json()["AccountListResponse"]["Accounts"]["Account"]
    if isinstance(accounts, dict):
        accounts = [accounts]
    return accounts[0]


def build_order_payload(args, client_order_id):
    order = {
        "allOrNone": "false",
        "priceType": "LIMIT" if args.limit else "MARKET",
        "orderTerm": "GOOD_FOR_DAY",
        "marketSession": "REGULAR",
        "Instrument": [
            {
                "Product": {"securityType": "EQ", "symbol": args.symbol.upper()},
                "orderAction": args.action,
                "quantityType": "QUANTITY",
                "quantity": str(args.quantity),
            }
        ],
    }
    if args.limit:
        order["limitPrice"] = str(args.limit)
    return {
        "orderType": "EQ",
        "clientOrderId": client_order_id,
        "Order": [order],
    }


def main():
    parser = argparse.ArgumentParser(description="Preview and place an equity order")
    parser.add_argument("action", choices=["BUY", "SELL"])
    parser.add_argument("symbol")
    parser.add_argument("quantity", type=int)
    parser.add_argument("--limit", type=float, help="limit price (default: market order)")
    parser.add_argument("--preview-only", action="store_true", help="preview without placing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    _, _, sandbox = ec.load_config()
    session, base = ec.get_session()
    account = first_account(session, base)
    account_id_key = account["accountIdKey"]
    print(f"Account: {account.get('accountDesc', '')} ({account['accountId']})")
    print(f"Environment: {'sandbox' if sandbox else 'PRODUCTION — REAL MONEY'}\n")

    # clientOrderId: unique per order, alphanumeric, max 20 chars
    client_order_id = uuid.uuid4().hex[:20]
    payload = build_order_payload(args, client_order_id)

    resp = session.post(
        f"{base}/v1/accounts/{account_id_key}/orders/preview.json",
        json={"PreviewOrderRequest": payload},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    preview = resp.json()["PreviewOrderResponse"]
    print("Preview:")
    print(json.dumps(preview, indent=2))

    if args.preview_only:
        return

    if not args.yes:
        prompt = f"\nPlace this {args.action} order for {args.quantity} {args.symbol.upper()}? [y/N] "
        if input(prompt).strip().lower() != "y":
            print("Cancelled.")
            return

    payload["PreviewIds"] = [
        {"previewId": p["previewId"]} for p in preview["PreviewIds"]
    ]
    resp = session.post(
        f"{base}/v1/accounts/{account_id_key}/orders/place.json",
        json={"PlaceOrderRequest": payload},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    print("\nOrder placed:")
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    main()
