# Setup Guide — HEB Grocery Agent

Follow these in order. Steps marked **[you]** need a human; everything else you can ask
the agent to do ("set up my tunnel", "run the dry-run checkout"). Software setup
(venv, MCPs, policy engine, repo) is already done — this guide is the path from here to
a working, always-on agent.

---

## Step 1 — HEB account **[you]** (~30 min)

1. Create/log into your account on [heb.com](https://www.heb.com).
2. Set your **home store** (the one you'd use for curbside).
3. Place **one small manual curbside order** end to end. This validates your account for
   online ordering, shows you the substitution/tip screens the agent will later drive,
   and gives the payment step below a real test.

## Step 2 — Your prepaid Mastercard **[you]** (~20 min)

You're using a store-bought prepaid Mastercard gift card loaded with cash. That works,
but two things are **mandatory** for online use:

1. **Register the card.** Go to the issuer's site printed on the back of the card
   (e.g. balance check / "register your card" URL) and add your **name and billing
   address**. Unregistered gift cards fail heb.com's address verification (AVS) and get
   declined even with sufficient balance. Use the same address as your HEB account.
   (This step stays manual — it's a different site per issuer.)
2. **Hand it to the agent** — the agent manages the HEB wallet for you. Two ways:
   - **Vault (recommended):** run `.venv/bin/python scripts/add_card.py` in Terminal —
     prompts locally (number/CVV hidden), stores in the macOS keyring. Then say
     **"switch HEB to my new card"**: the agent saves it on heb.com, sets it default,
     removes the old card, and deletes the keyring entry. The number never appears in
     chat, logs, or audit records (last-4 only).
   - **In chat:** just message the card details. Works, but they then persist in your
     conversation transcript — fine if you accept that for a low-balance gift card.

Gift-card realities to plan around:

- **Non-reloadable.** When the balance runs low: buy a new card → register it at the
  issuer → `add_card.py` (or paste in chat) → "switch HEB to my new card". ~3 minutes.
- **Keep ~25% buffer** above your typical order. Delivery orders pre-authorize above the
  cart total (tip + substitution headroom); a card with an exact-amount balance can
  decline at pre-auth even though the final charge would have fit.
- **Check the balance** at the issuer's site before big orders; the agent can't see it.
  (A declined card simply fails checkout — the audit log + notification will tell you.)
- This balance is also your final spending backstop, independent of the policy limits.

## Step 3 — First HEB login (one-time, ~5 min)

1. Open Claude in this project: `cd ~/Claude/grocery-agent && claude`
2. Say: **"Authenticate with HEB"** — the texas-grocery MCP opens a browser window;
   log in as yourself. The session is saved to `~/.texas-grocery-mcp/auth.json` and
   shared with the checkout server; credentials go to the macOS keyring.
3. Sanity check, still in chat: "search HEB for whole milk", "add a gallon to my cart",
   "what coupons can I clip?"

## Step 4 — Checkout verification (dry-run, with the agent)

Everything defaults to `HEB_CHECKOUT_DRY_RUN=true`: checkout walks all the way to the
final screen, screenshots it, and **stops one click before purchase**.

1. Ask: "preview my order for pickup" then "for delivery" — review totals and the saved
   card showing as the payment method.
2. Ask for a dry-run `place_order` for both pickup and delivery. Review screenshots in
   `data/orders/screenshots/`. If HEB's pages changed, the selectors in
   `src/heb_checkout/checkout_driver.py` (`SELECTORS` dict) get fixed against the real
   DOM — this is expected first-run work.
3. After 3 clean dry-runs of each type: one **live ~$20 order** in `approve` mode while
   you watch. Only after that, decide if/when to flip `HEB_CHECKOUT_DRY_RUN=false` in
   `.env` and `.mcp.json`.

## Step 5 — Always-on on this Mac (~10 min)

1. `cp config/.env.example .env`, set `MCP_BEARER_TOKEN` to a long random string
   (`openssl rand -hex 32`).
2. `make install-launchd` — starts the MCP server at login and a heartbeat every 30 min
   that pops a macOS notification if the agent is down, plus a **weekly check of PyPI
   for texas-grocery-mcp updates** (notifies; never auto-updates — the version is pinned).
3. Keep the Mac plugged in. `sudo pmset repeat wakeorpoweron MTWRFSU 07:45:00` wakes it
   before morning runs. (A closed, unplugged laptop runs nothing — that's the Phase 5
   Mac mini migration, already scripted via `make migrate`.)

## Step 6 — Phone ordering (~20 min) **[you + agent]**

1. Install cloudflared: `brew install cloudflared` (or the pkg installer).
2. `cloudflared tunnel login`, then `cloudflared tunnel create grocery-agent`.
3. Route a hostname you own (or use a Cloudflare-provided one) to
   `http://127.0.0.1:8787`, run the tunnel as a service.
4. On claude.ai → Settings → Connectors → **Add custom connector** →
   URL `https://<your-tunnel-host>/mcp`, auth header `Bearer <MCP_BEARER_TOKEN>`.
5. From the Claude phone app: "what's in my HEB cart?" — if that answers, phone ordering
   works end to end.

## Step 7 — Hands-off list intake (with the agent)

The agent reads grocery items from every configured source at once
(`read_grocery_lists` merges and dedupes them; `config/lists.yaml` configures them):

- **Apple Notes:** keep a note titled **Groceries**; the agent reads it and checks
  items off (✓) once ordered.
- **Apple Reminders:** a list named **Groceries** — this is also your **Siri channel**
  ("Hey Siri, add milk to my Groceries list"); the agent completes handled reminders.
- **Google Doc or Sheet:** share it "Anyone with the link – Viewer", paste the URL in
  `config/lists.yaml`. Works from any device/person you share the doc with (read-only —
  the agent can't check items off there).
- **Inbox file** (`data/inbox.md`): universal — anything that can write a text file
  (iCloud Drive, Dropbox, scripts) or POST to the gateway's `/list` endpoint feeds it.
  An **Apple Shortcut** ("Add to groceries" → Get Text → POST to
  `https://<tunnel-host>/list` with your bearer token) gives you a home-screen/Watch
  button.
- **iMessage** (off by default): text yourself "grocery: milk, limes" — enable in
  `config/lists.yaml` and grant Full Disk Access to the process running the agent.
  Caveat: some newer messages store text in a format the reader skips; keep triggers
  simple.
- **Todoist:** set `TODOIST_API_TOKEN` in `.env`; the agent reads your "Groceries"
  project and closes tasks once ordered.
- **Notion:** set `NOTION_API_TOKEN` + `NOTION_PAGE_ID` in `.env` (share the page with
  your integration); the agent reads unchecked to-dos and checks them off once ordered.
- **Email:** create a Gmail label **Groceries**; email yourself lists (read via your
  Gmail MCP on the host LLM side).
- **Standing order:** tell the agent your staples ("every week: milk, eggs, ...") — they
  live in `data/staples.json`. Then schedule the weekly run (e.g. "every Sunday at 9am,
  build my grocery order") via Claude scheduled tasks.

And two smart inputs that need no list at all:

- **Calendar awareness:** put your calendar's secret ICS URL in `.env`
  (`GROCERY_ICS_URLS`; Google Calendar → Settings → "Secret address in iCal format").
  When building orders the agent sees "Dinner party Saturday" and *proposes* extras —
  it never adds items without your yes.
- **Replenishment prediction:** once orders flow, the agent learns each item's purchase
  cycle from your receipts and flags what's due ("milk every ~7 days, last bought the
  4th — add it?"). Needs two purchases of an item before predicting.

macOS note: the first Notes/Reminders read triggers an automation permission prompt
("…wants to control Notes") — click OK once, per app.

## Step 8 — Dial in autonomy (anytime, in chat)

- "Switch grocery agent to auto under threshold" / "full auto" / "approve everything"
- "Set my weekly grocery limit to $250" · "Set per-order limit to $120"
- "Default to delivery" / "make this one pickup"

Current policy: `approve` mode, $200/order, $400/week, $1200/month, 1 order/day,
no orders 11pm–7am. See it live: "show my grocery policy".

## Ongoing maintenance

| What | How often | How |
|---|---|---|
| Card balance | before big orders | issuer's site (it's your hard backstop) |
| Upstream MCP updates | automatic weekly notification | or ask: "check for MCP updates" — review release notes, bump the pin in `pyproject.toml`, `make selftest` |
| Checkout still works | weekly, automatic before standing order | dry-run smoke; failures notify instead of silently skipping |
| Audit trail | whenever curious | "show my order history" or `data/orders/` |
