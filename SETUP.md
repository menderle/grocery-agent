# Setup Guide — HEB Grocery Agent

Follow these in order. Steps marked **[you]** need a human; everything else you can ask
the agent to do in chat ("set up my tunnel", "run the dry-run checkout"). Works with any
MCP-capable LLM client — Claude is the reference setup; for others see
[docs/INTEGRATION.md](docs/INTEGRATION.md).

---

## Step 0 — Install (one command, ~5 min)

```sh
git clone https://github.com/menderle/grocery-agent && cd grocery-agent
zsh scripts/install.sh
```

The installer handles everything machine-specific: Python (via uv, no admin needed),
pinned dependencies, the Playwright browser, a generated `.env` with a fresh
`MCP_BEARER_TOKEN`, the `.mcp.json` MCP registration for this machine's paths, and a
selftest proving the safety layer before anything touches a browser. Re-running it
is safe (after `git pull`, or on a migrated host).

Already installed? Skip ahead.

## Step 1 — HEB account **[you]** (~30 min)

1. Create/log into your account on [heb.com](https://www.heb.com).
2. Set your **home store** (the one you'd use for curbside).
3. Place **one small manual curbside order** end to end. This validates your account for
   online ordering, shows you the substitution/tip screens the agent will later drive,
   and gives the payment step below a real test.

## Step 2 — Payment card **[you]** (~20 min)

Reference setup: a store-bought prepaid Mastercard gift card loaded with cash. Two
things are **mandatory** for online use:

1. **Register the card** at the issuer's site printed on the back of the card, with
   your **name and billing address** (same address as your HEB account). Unregistered
   gift cards fail heb.com's address verification (AVS) and get declined even with
   sufficient balance. This step stays manual — it's a different site per issuer.
2. **Hand it to the agent** — the agent manages the HEB wallet for you:
   - **Vault (recommended):** run `.venv/bin/python scripts/add_card.py` in Terminal —
     prompts locally (number/CVV hidden), stores in the macOS keyring. Then say
     **"switch HEB to my new card"**: the agent saves it on heb.com, sets it default,
     removes the old card, and deletes the keyring entry. The number never appears in
     chat, logs, or audit records (last-4 only).
   - **In chat:** just message the card details. Works, but they then persist in your
     conversation transcript — fine if you accept that for a low-balance gift card.

Gift-card realities to plan around:

- **Non-reloadable.** When the balance runs low: buy a new card → register it →
  `add_card.py` → "switch HEB to my new card". ~3 minutes, no website visits.
- **Keep ~25% buffer** above your typical order. Delivery orders pre-authorize above
  the cart total (tip + substitution headroom); an exact-balance card can decline at
  pre-auth even though the final charge would have fit.
- **Check the balance** at the issuer's site before big orders; the agent can't see it.
  A declined card simply fails checkout — the audit log + notification will tell you.
- The card balance is your final spending backstop, independent of the policy limits.

(Alternative: a [Privacy.com](https://privacy.com) virtual card — merchant-locked,
reloadable, pausable — avoids every bullet above.)

## Step 3 — First HEB login (one-time, ~5 min)

1. Open your LLM client in this folder (Claude: `cd grocery-agent && claude` — the
   `.mcp.json` written by the installer registers the MCP servers automatically).
2. Say: **"Authenticate with HEB"** — a browser window opens; log in as yourself. The
   session is saved to `~/.texas-grocery-mcp/auth.json` and shared with the checkout
   server; credentials go to the macOS keyring.
3. Sanity check, still in chat: "search HEB for whole milk", "add a gallon to my cart",
   "what coupons can I clip?"

## Step 4 — Checkout verification (dry-run, with the agent)

Everything defaults to `HEB_CHECKOUT_DRY_RUN=true`: checkout walks all the way to the
final screen, screenshots it, and **stops one click before purchase**. A live order
also aborts itself if the on-screen total is unreadable or >10% over the previewed
total.

1. Ask: "preview my order for pickup" then "for delivery" — review totals and that the
   saved card (by last-4) shows as the payment method.
2. Ask for a dry-run `place_order` for both pickup and delivery. Review screenshots in
   `data/orders/screenshots/`. If HEB's pages changed, the selectors in
   `src/heb_checkout/checkout_driver.py` (`SELECTORS`) and `wallet.py`
   (`WALLET_SELECTORS`) get fixed against the real DOM — expected first-run work.
3. After 3 clean dry-runs of each type: one **live ~$20 order** in `approve` mode while
   you watch. Only after that, decide if/when to flip `HEB_CHECKOUT_DRY_RUN=false`
   (in both `.env` and `.mcp.json`).

## Step 5 — Always-on on this Mac (~5 min)

1. `make install-launchd` — two services start at login:
   - **`grocery-gateway --http`**: the full agent (shopping + checkout + lists) as one
     HTTP MCP endpoint on `127.0.0.1:8787`, bearer-token-protected, with `/health`.
   - **Heartbeat** every 30 min: macOS notification if the agent is down, plus a weekly
     PyPI check for texas-grocery-mcp updates (notifies only — the version is pinned;
     upgrades are always a deliberate pin bump + `make selftest`).
2. Keep the Mac plugged in. `sudo pmset repeat wakeorpoweron MTWRFSU 07:45:00` wakes it
   before morning runs. A closed, unplugged laptop runs nothing — that's what the
   Mac mini migration removes (`make migrate`, then `scripts/install.sh` + `make
   restore` on the new box; same for the Docker path).

## Step 6 — Phone ordering (~20 min) **[you + agent]**

1. Install cloudflared, then `cloudflared tunnel login` and
   `cloudflared tunnel create grocery-agent`.
2. Route a hostname (yours or Cloudflare-provided) to `http://127.0.0.1:8787` and run
   the tunnel as a service.
3. On claude.ai → Settings → Connectors → **Add custom connector** →
   URL `https://<your-tunnel-host>/mcp`, auth header `Bearer <MCP_BEARER_TOKEN from .env>`.
4. From the Claude phone app: "what's in my HEB cart?" — if that answers, phone
   ordering works end to end.

The same tunnel also exposes `POST /list` (the list drop-box used by Shortcuts and
webhooks below) and `GET /health`.

## Step 7 — Feeding the agent (pick the channels you'll actually use)

`read_grocery_lists` merges every configured source and dedupes;
`config/lists.yaml` + `.env` configure them. After an order, the agent checks items
off at their source.

| Channel | Setup | Agent checks off? |
|---|---|---|
| **Apple Notes** | keep a note titled "Groceries" | ✓ (marks lines, dates them) |
| **Apple Reminders** (= **Siri**: "add milk to my Groceries list") | a list named "Groceries" | ✓ (completes) |
| **Google Doc/Sheet** (shared household list) | share "Anyone with link – Viewer", URL in `lists.yaml` | read-only |
| **Inbox file** `data/inbox.md` | anything that writes text: iCloud/Dropbox/scripts | ✓ (clears) |
| **Apple Shortcut / webhooks** | POST text to `https://<tunnel>/list` with the bearer token | ✓ (via inbox) |
| **iMessage** ("grocery: milk, limes" to yourself) | enable in `lists.yaml` + Full Disk Access; off by default, some new-format messages are skipped | ✓ (marks processed) |
| **Todoist** | `TODOIST_API_TOKEN` in `.env`; "Groceries" project | ✓ (closes tasks) |
| **Notion** | `NOTION_API_TOKEN` + `NOTION_PAGE_ID` in `.env`; share page with the integration | ✓ (checks to-dos) |
| **Email** | Gmail label "Groceries" (read host-side via your Gmail MCP) | manual |
| **Standing order** | staples in `data/staples.json`; schedule "every Sunday 9am, build my grocery order" | n/a |

Two smart inputs that need no list at all:

- **Calendar awareness:** secret ICS URL(s) in `.env` (`GROCERY_ICS_URLS`; Google
  Calendar → Settings → "Secret address in iCal format"; iCloud → public calendar
  link). The agent sees "Dinner party Saturday" and *proposes* extras — it never adds
  items without your yes.
- **Replenishment prediction:** the agent learns each item's purchase cycle from your
  placed orders and flags what's due ("milk every ~7 days, last bought the 4th —
  add it?"). Starts predicting after the second purchase of an item.

macOS note: the first Notes/Reminders read triggers an automation permission prompt
("…wants to control Notes") — click OK once per app. If it's never granted, those
sources just report unavailable; nothing breaks.

## Step 8 — Dial in autonomy (anytime, in chat)

- "Switch grocery agent to auto under threshold" / "full auto" / "approve everything"
- "Set my weekly grocery limit to $250" · "Set per-order limit to $120"
- "Default to delivery" / "make this one pickup"

Defaults: `approve` mode, $200/order, $400/week, $1200/month, 1 order/day, no orders
11pm–7am. See it live anytime: "show my grocery policy". Spend limits are enforced in
code and block in **every** mode — approval cannot override them.

## Ongoing maintenance

| What | How often | How |
|---|---|---|
| Card balance | before big orders | issuer's site (it's your hard backstop) |
| Card swap when drained | as needed | register new card → `add_card.py` → "switch HEB to my new card" |
| Upstream MCP updates | automatic weekly notification | or ask "check for MCP updates" → review notes, bump pin in `pyproject.toml`, `make selftest` |
| Checkout still works | weekly, before the standing order | dry-run smoke; failures notify instead of silently skipping |
| After `git pull` | each time | `zsh scripts/install.sh` (idempotent) |
| Audit trail | whenever curious | "show my order history" or `data/orders/` |
| Moving hosts (Mac mini etc.) | once | `make migrate` on the old box; clone + `make restore` + `scripts/install.sh` on the new one |
