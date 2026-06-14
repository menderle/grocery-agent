# Local web UI (`make web`)

A self-hosted chat web app — a **second front door** alongside the Claude/MCP connector.
Both are clients of the same `grocery-gateway`, so the web app and Claude share the **same**
cart, spend limits, order history, approvals, and remembered picks. Nothing here can bypass
the money-safety guards — they live in `place_order`, which both interfaces call.

```
 Claude connector (OAuth/Funnel) ─┐
                                  ├─▶ grocery-gateway ─▶ HEB checkout · policy · approvals · prefs
 Local web UI (127.0.0.1:8788) ──┘    (shared state on disk + your HEB account)
```

The web app's brain is a **Claude API agent loop** (`src/grocery_web/`) that drives the
gateway in-process. That means it calls the Anthropic API and is **billed per token**,
separate from the Claude subscription that powers the connector.

## Run it

```sh
# one-time: put your Anthropic key in .env
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

make web        # → http://127.0.0.1:8788
```

The gateway/connector keep using port **8787**; the web UI uses **8788** — they coexist.

## What it does

- **Streaming chat** with the full agent: meal/headcount reasoning, search, cart, coupons,
  slots, preview, and policy-gated checkout.
- **Model picker** (top right) — switch between **Sonnet 4.6** (fast/cheap, default),
  **Opus 4.8** (most capable), and **Haiku 4.5** per conversation. Set the default and the
  visible set with `GROCERY_WEB_MODEL` / `GROCERY_WEB_MODELS` in `.env`.
- **Memory** — the agent uses `recall_item`/`get_preferences` to reuse your usual picks
  ("add my usual water" → your saved SKU) and `remember_item` when you choose a product.
  This memory is stored at the gateway level (`data/preferences.json`), so it works in the
  **Claude connector too** — teach it once, recall it anywhere.
- **Approval gate in the UI** — when an order needs sign-off, the chat shows the cart total
  and an **Approve & place order** button. Clicking it calls `place_order` with the approval
  id directly (one click → exactly one order), then the agent confirms the outcome.
- **Outcome banners** — green *placed*, amber *placed (unconfirmed — verify in HEB history)*,
  blue *dry-run rehearsed, not charged*, red *blocked by policy* / *aborted*.
- **Status header** — a **DRY-RUN / LIVE** badge, the policy mode, and rolling 7-day spend,
  so you always see whether real money can move. Honors `HEB_CHECKOUT_DRY_RUN` from `.env`.
- **Conversation memory** — chat threads are saved per browser to
  `data/web-conversations/` (gitignored), so a reload continues the same thread.

## Safety & shared state

- The web UI calls the **same** policy/approval/audit/checkout code as Claude. Spend caps,
  approval modes, quiet hours, the >10% overshoot abort, and dry-run all apply identically.
- A **cross-process file lock** (`data/.checkout.lock`, see `src/heb_checkout/locking.py`)
  serializes checkout, so the web UI and the Claude connector can never double-place an
  order or consume one approval twice.
- Keep `HEB_CHECKOUT_DRY_RUN=true` until you've watched a dry-run order go through.

## Remote access from your phone (private — not public)

The web app self-hosts on your Mac (it needs the live HEB browser session and your
residential IP — it cannot run on a serverless host). To reach it from your phone, use
**Tailscale Serve** (private to your tailnet), **not** Funnel (public):

```sh
# set a token first so even tailnet devices must present it
echo 'WEB_AUTH_TOKEN=<long-random-string>' >> .env
make web

# expose to YOUR devices only, over the encrypted tailnet (private):
tailscale serve --bg 8788
```

Then open the Tailscale URL on your phone with the token once:
`https://<your-machine>.<tailnet>.ts.net/?token=<long-random-string>` — the page stores the
token and uses it for all calls. This stays private to devices logged into your tailnet; it
is never exposed to the public internet.

> Why this differs from the phone **connector**: claude.ai connectors are reached by
> Anthropic's servers, so that endpoint must be public (hence Tailscale **Funnel** + OAuth).
> Your own web page is reached by *your* device, so a private tailnet + a static token is
> simpler and safer — no Google OAuth app, no public origin.

## Always-on (optional)

`make web` runs in the foreground. For always-on, an optional launchd job ships at
`deploy/launchd/com.grocery-agent.web.plist` (it is **not** auto-loaded by
`make install-launchd`). To enable it:

```sh
sed "s|__HOME__|$(pwd)|g" deploy/launchd/com.grocery-agent.web.plist \
  > ~/Library/LaunchAgents/com.grocery-agent.web.plist
launchctl load ~/Library/LaunchAgents/com.grocery-agent.web.plist   # logs: /tmp/grocery-web.log
```

## Config reference (`.env`)

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** The web UI's Claude API key (billed per token). |
| `WEB_PORT` | `8788` | Web UI port (gateway keeps 8787). |
| `WEB_BIND` | `127.0.0.1` | Bind address; leave local unless fronting with Tailscale. |
| `GROCERY_WEB_MODEL` | `sonnet` | Default model (`sonnet` / `opus` / `haiku`). |
| `GROCERY_WEB_MODELS` | `sonnet,opus,haiku` | Which models appear in the picker. |
| `WEB_AUTH_TOKEN` | (unset) | If set, every request needs it (Bearer or `?token=`); for remote access. |
