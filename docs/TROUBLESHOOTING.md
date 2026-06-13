# Troubleshooting & hard-won lessons

Everything that bit us in real use, with the fix. If something breaks, it's almost
certainly one of these. Grouped by symptom.

---

## Phone connector (Claude app)

### "Failed to start MCP authorization" / connector shows no tool-count number
The Claude app cached a stale auth requirement for the connector (usually because the
server returned a 401 at some earlier moment). It now keeps trying an OAuth flow the
server doesn't offer.
- **Fix:** delete the connector and **re-add it from scratch** (Settings → Connectors →
  remove HEB Grocery → Add custom connector again). A fresh add re-probes the server and
  connects cleanly. A connected connector shows a small tool-count number next to it
  (like Gmail's `12`).

### "I can't reach the H-E-B connector / its tools aren't loading" — but the backend works
The Claude app connects to a connector **once at the start of a conversation**. If that
first attempt failed (e.g. the session was briefly down), that whole chat stays stuck
believing the connector is down and keeps saying so — even after the backend recovers.
- **Fix:** start a **brand-new chat** and try again. Don't keep arguing with the stuck one.
- Confirm the backend is actually up first (on the Mac): `curl -s localhost:8787/health`.

### claude.ai connectors only support OAuth, not our bearer token
The "Add custom connector" dialog only has OAuth fields — no place for a static token.
- **Today's workaround:** `scripts/enable_phone_testing.sh` runs the gateway open
  (no auth) **but forces dry-run ON**, so nothing can be charged. The gateway refuses to
  run open if dry-run is off (hard guard). Run `scripts/lock_down.sh` when done.
- **The real fix (pending):** implement OAuth on the gateway (Google login, restricted to
  your email) — then real phone ordering is safe. See the OAuth task.

---

## HEB session ("401 Unauthorized", tools error out)

The HEB session expires fast (~10–15 min) and HEB's Incapsula bot wall blocks automated
re-logins. Full background: `docs/SESSION-AND-HASHES.md`.
- **Normal operation:** a genuine Chrome is **parked** and logged in
  (`scripts/start_parked_chrome.sh`); `scripts/sync_parked_session.py` copies its live
  session into `auth.json` **every 3 minutes** (launchd `com.grocery-agent.session-sync`).
- **If tools 401:** run `.venv/bin/python scripts/sync_parked_session.py` to refresh now.
- **If that says "logged OUT":** the parked Chrome lost its login — re-run
  `scripts/start_parked_chrome.sh`, log in again, leave the window open.

## Stale HEB API hashes ("Persisted query hash … no longer valid")

HEB rotates its internal GraphQL hashes on site deploys, breaking cart/search.
- **Fix:** `.venv/bin/python scripts/refresh_graphql_hashes.py` (harvests fresh hashes
  into `config/graphql-hashes.json`; the `scripts/shop-server` launcher applies them).
- Mutation hashes (`cartItemV2` add-to-cart, `SelectPickupFulfillment` store-change) only
  appear when that action actually fires — `capture_real_session.py` drives those actions
  during login to grab them all in one go.

## Wrong store / "no default store is set"

`store_change` is **permanently broken** in this library (stale `SelectPickupFulfillment`
hash) — but it's also **unnecessary**: the store is fixed by whatever the parked Chrome
session is set to.
- The cart and orders use the **parked session's store** (set it once in the parked Chrome
  window: heb.com → choose your curbside store).
- Search *pricing* needs `store_id` passed explicitly — the gateway instructions tell the
  model to always pass it. The agent is told to **never call store-change** (it fails and
  isn't needed).

---

## Always-on requirements (what must be true for it to work)

At the moment of any request, on the host Mac:
1. **Awake & online** — lid-closed-unplugged = everything dead.
2. **Gateway running** — launchd `com.grocery-agent.server` (auto-starts at login).
3. **Parked Chrome running & logged into HEB** — the session source.
4. **Tailscale Funnel up** — for phone access (`scripts/setup_tailscale_funnel.sh`).
5. **Dry-run / auth posture** as intended (test vs real).

This laptop-must-be-awake limit is exactly what the **Phase 5 Mac mini** removes.

## Security posture cheat-sheet

| Mode | Auth | Dry-run | Safe to leave on? |
|---|---|---|---|
| Test (now) | none | ON (forced) | yes — no charge possible, but data is readable by anyone with the URL |
| Real (after OAuth) | OAuth (you only) | OFF | yes |
| **Never** | none | OFF | **blocked in code** |

Get the bearer token (when auth is on): `grep MCP_BEARER_TOKEN .env`.

---

## Environment gotchas (first install)

- System Python may be too old (HEB needs 3.10+); the installer uses **uv** to get 3.12.
  No Homebrew needed.
- Playwright needs the **full Chromium** download (~100 MB) the first time.
- macOS will prompt once for **automation permission** (Notes/Reminders) and **Screen
  Recording / Accessibility** (only if using computer-use) — grant once.
