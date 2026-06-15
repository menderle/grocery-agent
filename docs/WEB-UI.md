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
- **Settings sheet (the gear, top-right)** — holds the **model picker** (Sonnet 4.6 default /
  Opus 4.8 / Haiku 4.5, per conversation; defaults via `GROCERY_WEB_MODEL` /
  `GROCERY_WEB_MODELS`), a **status panel** (Safety, Autonomy, H-E-B session, 7-day spend,
  Store), an **autonomy control**, a **Help/FAQ**, and **New conversation**.
- **Autonomy control ("When you order")** — choose *Ask me to approve each order* (default),
  *Auto-place small orders, ask for big ones*, or *Place orders automatically*. This sets your
  policy mode via the token-gated `POST /api/settings`. Spend caps still hard-block over-limit
  orders in every mode, and Test mode (dry-run) still gates real charges.
- **Memory** — the agent uses `recall_item` / `get_preferences` to reuse your usual picks
  ("add my usual water" → your saved SKU) and `remember_item` / `forget_item` /
  `add_staple` / `remove_staple` to learn them. Stored at the gateway level
  (`data/preferences.json`), so it works in the **Claude connector too** — teach it once,
  recall it anywhere.
- **Approval gate in the UI** — when an order needs sign-off, the chat shows the itemized cart
  + total and an **Approve & place** button. Clicking it calls `place_order` with the approval
  id directly (one click → exactly one order), then the agent confirms the outcome.
- **Outcome banners** — green *placed*, amber *placed (unconfirmed — verify in HEB history)*,
  blue *dry-run rehearsed, not charged*, red *blocked by policy* / *aborted*.
- **Header** — shows your store and a prominent **DRY-RUN / LIVE** badge (LIVE turns the app
  chrome red) so the real-money state is always visible. Policy mode + 7-day spend live in the
  settings sheet. Honors `HEB_CHECKOUT_DRY_RUN` from `.env`.
- **Conversation memory** — chat threads are saved per browser to
  `data/web-conversations/` (gitignored), so a reload continues the same thread.

## Safety & shared state

- The web UI calls the **same** policy/approval/audit/checkout code as Claude. Spend caps,
  approval modes, quiet hours, the >10% overshoot abort, and dry-run all apply identically.
- **The autonomous agent cannot change policy or payment methods.** `set_policy`,
  `update_payment_card`, and `remove_payment_card` are *always* withheld from the web agent
  loop (a hardcoded floor — `GROCERY_WEB_TOOL_DENY` can only add more) so prompt-injected tool
  content (e.g. a crafted product name) can't raise your spend caps or swap your card. You, the
  human, can still set the **autonomy mode** from the settings sheet (token-gated
  `/api/settings`); spend caps and dry-run stay in force. Change spend-limit *amounts* via the
  Claude connector ("set my weekly limit to $250") or `config/policy.yaml`.
- A **cross-process lock** (`data/.checkout.lock`, see `src/heb_checkout/locking.py`,
  acquired off the event loop) serializes checkout, so the web UI and the Claude connector
  can never double-place an order or consume one approval twice.
- **Duplicate orders are skipped:** an identical cart (same items + total) placed within
  ~15 minutes returns `duplicate_skipped` instead of charging again.
- Keep `HEB_CHECKOUT_DRY_RUN=true` until you've watched a dry-run order go through.

## Remote access from your phone (private — not public)

The web app self-hosts on your Mac (it needs the live HEB browser session and your
residential IP — it cannot run on a serverless host). To reach it from your phone, put both
devices on your **Tailscale tailnet** (install the Tailscale app on the phone, sign in to the
same account) and bind the app to the tailnet interface:

```sh
echo 'WEB_AUTH_TOKEN=<long-random-string>' >> .env   # required for any non-loopback access
echo 'WEB_BIND=0.0.0.0' >> .env                       # listen on the tailnet interface too
# restart: launchctl unload/load the web job, or re-run `make web`
```

Then open this on the phone (find the Mac's tailnet IP with `tailscale ip -4`):

    http://<mac-tailnet-ip>:8788/?token=<your-token>

The page saves the token (after that the bare `http://<ip>:8788` works) and sends it as an
`Authorization: Bearer` header on every API call. Traffic rides Tailscale's encrypted tunnel,
so plain `http` to the `100.x` address is private — never on the public internet. (Safari may
label the IP "Not secure"; that's expected and fine on a tailnet.)

> The token is a static secret — treat the `?token=` link like a password (don't paste it into
> shared chats), and rotate it any time by changing `WEB_AUTH_TOKEN` and restarting.
>
> Tip: avoid `tailscale serve`/Funnel for this. Funnel is public, and on a node that already
> Funnels the Claude connector, a phone can resolve the MagicDNS name to the public ingress —
> the raw tailnet IP above sidesteps that entirely.
>
> Why this differs from the phone **connector**: claude.ai connectors are reached by
> Anthropic's servers, so that endpoint must be public (Tailscale **Funnel** + OAuth). Your
> own web page is reached by *your* device, so a private tailnet + a token is simpler and safer.

## Always-on (optional)

`make web` runs in the foreground. For always-on, an optional launchd job ships at
`deploy/launchd/com.grocery-agent.web.plist` (it is **not** auto-loaded by
`make install-launchd`). To enable it:

```sh
sed "s|__HOME__|$(pwd)|g" deploy/launchd/com.grocery-agent.web.plist \
  > ~/Library/LaunchAgents/com.grocery-agent.web.plist
launchctl load ~/Library/LaunchAgents/com.grocery-agent.web.plist   # logs: /tmp/grocery-web.log
```

## Add to home screen (installable app)

The web UI ships a web manifest + slate app icons, so it installs as a standalone app:
on iPhone Safari open the URL → Share → **Add to Home Screen**. It then launches full-screen
(no browser chrome), follows system light/dark, and shows the app icon. The UI is mobile-first
— safe-area aware, keyboard-aware (`visualViewport`), and responsive from small phones to desktop.

## Config reference (`.env`)

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** The web UI's Claude API key (billed per token). |
| `WEB_PORT` | `8788` | Web UI port (gateway keeps 8787). |
| `WEB_BIND` | `127.0.0.1` | Set `0.0.0.0` for phone access over your tailnet (requires `WEB_AUTH_TOKEN`). |
| `GROCERY_WEB_MODEL` | `sonnet` | Default model (`sonnet` / `opus` / `haiku`). |
| `GROCERY_WEB_MODELS` | `sonnet,opus,haiku` | Which models appear in the picker. |
| `WEB_AUTH_TOKEN` | (unset) | Required for any non-loopback access. Sent as `Authorization: Bearer` (or `?token=` on first load). Rotate by changing it + restarting. |
| `GROCERY_WEB_TOOL_DENY` | (unset) | Extra tools to hide from the web agent. `set_policy` + wallet tools are **always** denied; this only adds more. |
