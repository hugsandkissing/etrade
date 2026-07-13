"""Interactive OAuth login for the E*TRADE API.

Run this once per day (tokens expire at midnight US Eastern):

    python auth.py

It prints an authorization URL — open it in a browser, log in to E*TRADE,
accept, and paste the verification code back here. Tokens are saved to
tokens.json (gitignored).
"""

from requests_oauthlib import OAuth1Session

import etrade_client as ec


def main():
    key, secret, sandbox = ec.load_config()
    env = "SANDBOX" if sandbox else "PRODUCTION (live account!)"
    print(f"Environment: {env}")

    session = OAuth1Session(key, client_secret=secret, callback_uri="oob")
    request_token = session.fetch_request_token(ec.REQUEST_TOKEN_URL)

    url = f"{ec.AUTHORIZE_URL}?key={key}&token={request_token['oauth_token']}"
    print("\n1. Open this URL in your browser and log in to E*TRADE:\n")
    print(f"   {url}\n")
    verifier = input("2. Paste the verification code shown after you accept: ").strip()

    session = OAuth1Session(
        key,
        client_secret=secret,
        resource_owner_key=request_token["oauth_token"],
        resource_owner_secret=request_token["oauth_token_secret"],
        verifier=verifier,
    )
    tokens = session.fetch_access_token(ec.ACCESS_TOKEN_URL)
    ec.save_tokens(tokens)
    print("\nSaved tokens to tokens.json — valid until midnight US Eastern.")


if __name__ == "__main__":
    main()
