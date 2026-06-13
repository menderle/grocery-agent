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

### OAuth (the real fix — Google login restricted to your email)
The gateway uses FastMCP `GoogleProvider` (an OAuth proxy) + an `OwnerOnly` middleware
allowlist (`src/heb_checkout/auth.py`). Connector OAuth fields stay **blank** (dynamic
registration). Debug in this order:

- **Verify discovery over the PUBLIC URL** (`$B` = your `OAUTH_BASE_URL`):
  - `curl -s -X POST $B/mcp -d '{}'` → **401** with `WWW-Authenticate: Bearer … resource_metadata="$B/.well-known/oauth-protected-resource/mcp"`.
  - `curl -s $B/.well-known/oauth-protected-resource/mcp` → lists `authorization_servers`.
  - `curl -s $B/.well-known/oauth-authorization-server` → has `authorization_endpoint`,
    `token_endpoint`, `registration_endpoint`, and `S256` in `code_challenge_methods_supported`.
  - `curl -s $B/health` → 200 (must stay public — `OwnerOnly` must NOT gate it).
- **`OAUTH_BASE_URL` must be the bare public https origin** — no `/mcp`, no trailing
  slash, https not http. Wrong shape breaks discovery. (The code rejects http/`/mcp`.)
- **`redirect_uri_mismatch` at Google** → you must register exactly `<OAUTH_BASE_URL>/auth/callback`
  in the Google app. Claude's own callback (`https://claude.ai/api/mcp/auth_callback`) is
  set in the code's `allowed_client_redirect_uris`, NOT in Google.
- **A different Google account gets 403** → intended; only `OAUTH_ALLOWED_EMAILS` connect.
- **Auth "succeeds" then every call 401s** → audience (RFC 8707) mismatch; leave
  `resource_base_url` unset so it defaults to `base_url`.
- **`invalid_scope`** → mitigated by `required_scopes == valid_scopes` in auth.py (fastmcp #1794).
- **Connects in Claude Code but "disconnected" on claude.ai/Desktop** (fastmcp
  #2224/#1919/#1737) → watch `/tmp/heb-checkout.log` during the dance; the no-auth
  dry-run fallback (`enable_phone_testing.sh`) is your stopgap.
- **Re-login after every restart** → `client_storage` (disk) persists DCR clients+tokens;
  if you see this, confirm `py-key-value-aio[disk]` is installed and `state/oauth` is writable.

### Why the endpoint must be PUBLIC (and Funnel isn't "private")
The Claude app's custom connector is reached **by Anthropic's servers, not your phone
directly** — it's server-side. So the gateway URL must be reachable from the public
internet. That's why we use **Tailscale Funnel** (which exposes publicly, gated by
token/OAuth), *not* plain private Tailscale — a tailnet-only address is unreachable by
Anthropic's backend and the connector would never connect. Cloudflare tunnel and Tailscale
Funnel have the **same** "public, auth-gated" posture; Funnel just gives a stable URL.

### Custom connectors require a paid Claude plan
Pro / Max / Team / Enterprise. On a free plan the "Add custom connector" option doesn't
appear. (The agent still works fully from **Claude Code on the Mac** with no plan gate —
the connector is only for the phone/web app.)

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

### After a reboot (or if Chrome crashes)
The four launchd jobs (`server`, `heartbeat`, `session-sync`, `parked-chrome`) all start
at login. `parked-chrome` re-launches the Chrome window automatically (idempotent, every
5 min):
- If the profile's HEB login **survived**, the agent is fully back with no action.
- If HEB **logged it out**, the window opens logged-out; the heartbeat notifies you, and
  you re-run `scripts/start_parked_chrome.sh` (or just log in again in the window).
- Re-`make install-launchd` after a fresh clone or `git pull` to (re)register all four.

## Tool quirks worth knowing (when scripting against the shop tools directly)

- `cart_add` and `cart_remove` are two-step: the first call returns a **preview**; pass
  `confirm=true` to actually do it.
- `cart_add` wants **both** `product_id` (short id) **and** `sku_id` (longer id) from the
  same `product_search` result, or it may report "added" without verifying.
- **Variable-weight items** (bananas "avg 2.4 lbs", deli meats) make the cart subtotal and
  the checkout total differ by a little — normal. `place_order`'s 10%-over guard tolerates
  it; a bigger gap aborts the order rather than charging blind.
- Always pass `store_id` to `product_search` (the gateway instructions enforce this).
- The model on the phone/desktop handles all of the above automatically — these notes are
  for when you call the tools directly in a script.

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
