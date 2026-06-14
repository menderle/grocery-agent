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

## Step 3 — HEB login: the parked browser (one-time, ~5 min)

HEB's bot detection blocks automated logins, so the agent reads its session from a
**genuine Chrome window that stays running** (this is what makes the agent reliable —
a live real browser keeps the session naturally alive; only cold relaunches get blocked):

1. `zsh scripts/start_parked_chrome.sh` — a real Chrome opens on the HEB login page.
2. Log in (**check "keep me signed in"**), set your home store, then just **leave the
   window open** (minimized is fine).
3. The agent syncs cookies from it automatically (`sync_parked_session.py`, run by the
   heartbeat) — and harvests HEB's current API hashes at the same time.
4. Sanity check in chat: "search HEB for whole milk", "add a gallon to my cart".

If the parked window ever gets logged out or closed, the heartbeat notifies you;
re-run steps 1–2 (~2 min).

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

## Step 6 — Phone ordering with OAuth (~25 min) **[you + agent]**

The Claude app's custom connector authenticates via **OAuth only** (no token field), so
the gateway uses Google login restricted to your email. One-time setup:

**a. Public URL** — run `zsh scripts/setup_tailscale_funnel.sh` (Tailscale Funnel; gives a
stable `https://<machine>.<tailnet>.ts.net`). That origin is your `OAUTH_BASE_URL`.

**b. Google OAuth app** (Google Cloud Console, free, ~10 min):
1. New project → **APIs & Services → OAuth consent screen** → **External**, leave in
   **Testing**, add your Gmail as a **Test user**.
2. **Credentials → Create OAuth client ID → Web application**.
3. **Authorized redirect URI** = `<OAUTH_BASE_URL>/auth/callback`
   (e.g. `https://maurices-macbook-air.taile913b1.ts.net/auth/callback`). Nothing else.
4. Copy the **Client ID** and **Client secret**.

**c. Configure** `.env`: set `OAUTH_BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `OAUTH_ALLOWED_EMAILS=you@gmail.com`; **remove**
`MCP_ALLOW_NO_AUTH`. Restart: `make install-launchd` (or reload the server job).

**d. Add the connector** — claude.ai → Settings → Connectors → **Add custom connector** →
URL `<OAUTH_BASE_URL>/mcp`, **both OAuth fields BLANK** (dynamic registration). Save →
**Connect** → sign in with your allowed Google account → Connected. (A different Google
account signs in at Google but the server returns 403 — by design.)

**e. Test** from the phone: "what's in my HEB cart?" — answers = OAuth works end to end.

Until you finish OAuth, the temporary test path is `zsh scripts/enable_phone_testing.sh`
(open + dry-run-forced-on, no charge possible); `scripts/lock_down.sh` reverts it. The
same tunnel also exposes `POST /list` (Shortcuts/webhooks; uses `LIST_DROP_TOKEN`) and the
public `GET /health`. Full OAuth troubleshooting: `docs/TROUBLESHOOTING.md`.

## Step 6b — On-demand delivery via Favor (optional, ~15 min)

Favor (H-E-B-owned) does ~20-45 min ("now") or ~2h ("express") delivery, up to 25 items —
the "I need X in the next hour" path. HEB scheduled delivery stays primary for stock-ups.
It's a **separate Favor account** and runs only when you opt in.

1. **Create a Favor account** — phone-number + SMS signup at favordelivery.com (or the app).
   Add your delivery address and a payment card on the Favor account.
2. **Park a logged-in Favor browser:** `zsh scripts/start_parked_favor_chrome.sh` → log in
   in that window (separate profile/port 9223 from HEB), leave it open.
3. `.venv/bin/python scripts/sync_parked_favor_session.py` → saves the Favor session.
4. In `.env`: set `FAVOR_DEFAULT_ADDRESS=...` (Favor is address-keyed, not store-keyed).
5. `make favor-enable` — installs the parked-Favor-Chrome + favor-session-sync launchd jobs.
6. From chat/phone: *"I need limes and tortillas from Favor in the next hour"* → the
   `favor_*` tools search → cart → preview → **approval gate (same spend limits)** → place.
   Defaults to `FAVOR_CHECKOUT_DRY_RUN=true`; verify the checkout selectors against your real
   Favor login (same first-run step HEB took), then flip to false for real on-demand orders.

Until set up, the `favor_*` tools just report "not configured" — harmless. Check anytime:
ask the agent "favor status".

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
| **Apple Shortcut / webhooks** | POST text to `https://<tunnel>/list` with `LIST_DROP_TOKEN` | ✓ (via inbox) |
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
