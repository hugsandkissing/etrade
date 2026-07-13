# E*TRADE API starter

Minimal Python setup for calling the E*TRADE API: OAuth login, account
listing, and quotes.

## How E*TRADE API access works

E*TRADE does not use a simple API key. Access requires three things:

1. **Consumer key + consumer secret** — issued at
   [developer.etrade.com](https://developer.etrade.com). Sandbox keys are
   instant; production keys require completing E*TRADE's API user intent
   survey and agreements.
2. **A daily OAuth login** — an access token is obtained by logging in to
   E*TRADE in a browser and pasting back a verification code. Tokens
   **expire at midnight US Eastern every day** and go dormant after 2 hours
   of inactivity. There is no way around the daily browser login; it's a
   deliberate security feature.
3. **Signed requests** — every API call is signed with OAuth 1.0a
   (HMAC-SHA1). The libraries here handle that.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your consumer key/secret
```

Keep `ETRADE_SANDBOX=true` until everything works — the sandbox returns
canned data and cannot touch your real account.

## Daily login

```bash
python auth.py
```

Open the printed URL, log in, accept, paste the verification code back.
Tokens are saved to `tokens.json` (gitignored) and are valid until
midnight US Eastern.

## Try it

```bash
python example.py        # lists accounts + quotes AAPL
python example.py TSLA
```

## Security notes

- Never commit `.env` or `tokens.json` (both are gitignored).
- Never paste your consumer secret into chats, issues, or PRs. For Claude
  Code sessions, set `ETRADE_CONSUMER_KEY` and `ETRADE_CONSUMER_SECRET` as
  environment variables in the environment configuration instead.
- Production keys can place real trades. Test everything in sandbox first.
