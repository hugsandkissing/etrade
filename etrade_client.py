"""Shared E*TRADE API client helpers.

E*TRADE uses OAuth 1.0a (HMAC-SHA1), not a bearer-token API key. The
consumer key/secret identify your app; a user access token (obtained via
auth.py) authorizes calls against your account. Access tokens expire at
midnight US Eastern and go dormant after 2 hours of inactivity (renewable
until midnight via renew_access_token).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session

TOKEN_FILE = Path(__file__).parent / "tokens.json"

# OAuth endpoints live on the production host even for sandbox keys;
# the consumer key determines which environment you're actually in.
OAUTH_BASE = "https://api.etrade.com"
REQUEST_TOKEN_URL = f"{OAUTH_BASE}/oauth/request_token"
ACCESS_TOKEN_URL = f"{OAUTH_BASE}/oauth/access_token"
RENEW_TOKEN_URL = f"{OAUTH_BASE}/oauth/renew_access_token"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"


def load_config():
    load_dotenv()
    key = os.environ.get("ETRADE_CONSUMER_KEY")
    secret = os.environ.get("ETRADE_CONSUMER_SECRET")
    if not key or not secret:
        raise SystemExit(
            "Missing ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET. "
            "Copy .env.example to .env and fill them in, or export them "
            "as environment variables."
        )
    sandbox = os.environ.get("ETRADE_SANDBOX", "true").lower() != "false"
    return key, secret, sandbox


def api_base(sandbox):
    return "https://apisb.etrade.com" if sandbox else "https://api.etrade.com"


def save_tokens(tokens):
    TOKEN_FILE.write_text(json.dumps(tokens))
    TOKEN_FILE.chmod(0o600)


def get_session():
    """Return an authenticated session using tokens saved by auth.py."""
    key, secret, sandbox = load_config()
    if not TOKEN_FILE.exists():
        raise SystemExit("No saved tokens. Run: python auth.py")
    tokens = json.loads(TOKEN_FILE.read_text())
    session = OAuth1Session(
        key,
        client_secret=secret,
        resource_owner_key=tokens["oauth_token"],
        resource_owner_secret=tokens["oauth_token_secret"],
    )
    return session, api_base(sandbox)
