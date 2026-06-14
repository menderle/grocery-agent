# 🛒 Grocery Agent

An always-on personal agent that **buys your groceries from H‑E‑B** — you just talk to it.
Message Claude on your phone *"order hot dogs for 8 people"* and it figures out the
quantities, asks about buns and condiments, builds the cart at your store, and checks out —
paying with a prepaid card you set once. No app-tapping, no forms.

It runs as **MCP servers** (Model Context Protocol), so it works with Claude on desktop and
phone, and with any other MCP-capable assistant. Money safety is enforced in code, not by
prompting.

> **Status:** working and in real use for H‑E‑B curbside/scheduled delivery. On-demand
> (Favor) is semi-automated. It's a personal, self-hosted project — not affiliated with
> H‑E‑B or Favor. See [Known limitations](#known-limitations).

---

## What's possible

**Talk to it however's easiest:**
- **Chat / voice with Claude** (desktop or phone) — *"add oat milk and eggs"*, *"order the usual"*.
- **A self-hosted web app** — `make web` opens a local chat UI (pick Sonnet/Opus/Haiku per chat) that shares the *same* cart, spend limits, history, and remembered picks as the Claude path. See [docs/WEB-UI.md](docs/WEB-UI.md).
- **Siri / Apple Reminders** — *"Hey Siri, add limes to my Groceries list"*; the agent reads it.
- **Apple Notes, a shared Google Doc, Todoist, Notion, email, or a text file** — jot items anywhere; it merges them all.
- **A weekly standing order** — re-orders your staples on a schedule, adjusting for what you added during the week.

**Two ways to get the food:**
- **H‑E‑B curbside or scheduled delivery — fully automated.** Search → cart → coupons → pick a time → checkout → place. Hands-off.
- **Favor on-demand (~20–45 min / ~2 h) — semi-automated.** The agent finds and adds your items; you tap Place in the Favor app (Favor requires an SMS code at checkout that only you can complete).

**It's actually smart about it:**
- **Meal & headcount reasoning** — *"taco night for 6"* becomes the right quantities, and it asks about the obvious extras before ordering.
- **Calendar-aware** — sees *"dinner party Saturday"* on your calendar and offers to add supplies.
- **Replenishment** — learns your purchase cycles from past orders (*"you're about due for milk — add it?"*).
- **Coupons & substitutions** — clips applicable digital coupons; follows your substitution preferences.
- **Slot suggestions** — proposes pickup/delivery times that fit your schedule.

**You stay in control:**
- **Autonomy is a toggle** — `approve` (confirm every order), `auto under a threshold`, or `full auto`.
- **Spend limits enforced in code** — per-order, rolling weekly, and monthly caps that block in *every* mode (approval can't override them).
- **Dry-run by default** — checkout stops one click before purchase and screenshots it, until you turn real ordering on.
- **Prepaid card** — the agent never sees card numbers; the card's own balance is the final backstop.

---

## What you need before it can place an order

Ordering groceries touches a real account and real money, so a few things must be set up.
**[SETUP.md](SETUP.md) is the step-by-step guide** — this is the overview of what's required.

**Required (to order at all, from your computer):**
1. **An H‑E‑B account with a home store**, and one manual curbside order placed yourself —
   this activates your account for online ordering.
2. **A payment method saved on your H‑E‑B account** — a [Privacy.com](https://privacy.com)
   virtual card (recommended) or a registered reloadable prepaid Visa/Mastercard. You enter
   it once on heb.com; it never enters the agent.
3. **A Mac to run it on** (kept awake) and the one-command install (below).
4. **A one-time H‑E‑B login** in a real browser the agent then reuses (H‑E‑B blocks
   automated logins, so this is done by hand once).
5. **Your home store id** in `config/store.json` (the installer creates it from a template;
   ask the agent *"search HEB stores near <your address>"* to find your id).

**Additional, only if you want to order from your phone:**
6. **A Google OAuth app** (free, ~10 min) so the phone connector authenticates *you only*, and
   **a Tailscale tunnel** to reach your Mac. (Phone connectors need a paid Claude plan.)

**Additional, only for Favor on-demand (optional):**
7. **A Favor account** (separate phone+SMS signup) and a one-time login. Remember: Favor
   orders are *prepared* by the agent but *placed* by you (SMS gate).

Until set up, nothing can be charged — and `HEB_CHECKOUT_DRY_RUN=true` keeps it that way
while you verify everything.

---

## Quick start

```sh
git clone <this repo> grocery-agent && cd grocery-agent
zsh scripts/install.sh        # Python (via uv), deps, Chromium, secrets, store template, selftest
```

Then follow **[SETUP.md](SETUP.md)** for the account/login/card steps above. Works with any
MCP-capable LLM client; `grocery-gateway` exposes the whole agent as one server a larger
personal-assistant agent can mount as its "grocery" capability.

---

## How it works

```
 You (chat / phone / Siri / email / schedule)
        │
        ▼
 grocery-gateway  ── one MCP endpoint, OAuth-gated for remote/phone ──┐
   • texas-grocery-mcp  → H-E-B GraphQL (search, cart, coupons, store)  │ runs on your Mac
   • heb-checkout       → Playwright → heb.com checkout (policy-gated)   │ (Tailscale for phone)
   • favor-checkout     → favordelivery.com (build cart; you place)      │
   • policy engine + approvals + audit (the money-safety core)          │
        │                                                                │
        ▼                                                                │
 Payment: a prepaid card saved on your H-E-B account ────────────────────┘
```

The checkout half drives a real logged-in browser session (kept warm automatically). The
**only** code path that spends money is `place_order`, and it consults your policy + spend
limits before every order.

| Area | Where |
|---|---|
| Checkout, policy, approvals, audit, wallet | `src/heb_checkout/` |
| On-demand (Favor) | `src/favor_checkout/` |
| One MCP endpoint for everything | `grocery-gateway` (`src/heb_checkout/gateway.py`) |
| Local web UI (`make web`) | `src/grocery_web/` (Claude agent loop over the gateway) |
| Autonomy mode + spend limits | `config/policy.yaml` |
| List intake sources | `config/lists.yaml`, `src/heb_checkout/lists.py` |
| Setup / migration / always-on | `scripts/`, `deploy/`, `Makefile`, `Dockerfile` |

---

## Known limitations

- **Favor can't auto-place orders.** Favor requires SMS verification on *every* checkout
  (a fraud gate — confirmed even for a logged-in, pre-verified session). Favor is
  semi-automated: the agent builds the cart, you tap Place + enter the code. H‑E‑B scheduled
  curbside/delivery is the fully hands-off path.
- **Always-on needs the host awake.** The agent and its browser session run on *your* Mac
  (launchd + Tailscale), not in the cloud. If it sleeps, the phone/remote path is down. A
  dedicated always-on box (e.g. a Mac mini) removes that.
- **H‑E‑B automation is unofficial.** Checkout drives heb.com via a real browser; it can
  break on site changes and could trip bot detection or run afoul of H‑E‑B's Terms. Money
  safeguards: dry-run default, the approval gate, and the spend limits in `config/policy.yaml`.

---

## Docs

- **[SETUP.md](SETUP.md)** — full step-by-step first-run (accounts, card, login, phone, Favor).
- **[docs/SHARING.md](docs/SHARING.md)** — what to personalize before handing it to someone else.
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — every pitfall hit + the fix.
- **[docs/SESSION-AND-HASHES.md](docs/SESSION-AND-HASHES.md)** — the two H‑E‑B gotchas (session, API hashes).
- **[docs/INTEGRATION.md](docs/INTEGRATION.md)** — wiring into any LLM / phone / personal-assistant agent.
- **[docs/WEB-UI.md](docs/WEB-UI.md)** — the self-hosted local web app (`make web`): model picker, memory, private remote access.
- **[prompts/system-prompt.md](prompts/system-prompt.md)** — the agent's operating instructions.

## Safety model (in brief)

`place_order` is the only money path; it checks `config/policy.yaml` + the audit log before
every order — mode (`approve` / `auto_under_threshold` / `full_auto`), per-order / weekly /
monthly spend caps (hard, not approval-overridable), max orders per day, quiet hours. Live
orders abort if the on-screen total exceeds the approved total by >10%. Card numbers never
enter the agent (the agent selects your saved card; wallet logs are last-4 only). Adjust by
asking: *"switch to full auto"*, *"set my weekly limit to $250"*.

Verify the safety core anytime: `make selftest` (no network, no browser).
