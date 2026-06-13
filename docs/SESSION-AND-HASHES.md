# HEB session & GraphQL hashes — the two live-environment gotchas

Both are solved with scripts; this explains when to re-run them.

## 1. Login / session (HEB bot detection)

HEB's Incapsula WAF fingerprints Playwright-launched Chromium and blocks it
(error 15 / 401), so texas-grocery-mcp's own headless auto-login does **not** work
cold. The working approach captures a session from your **real Chrome**:

```sh
.venv/bin/python scripts/capture_real_session.py
```

Launches genuine Chrome (debug port + dedicated profile under `profiles/heb-chrome`),
you log in by hand, and it writes cookies + the reese84 trust token to
`~/.texas-grocery-mcp/auth.json`.

**Refresh reliability (important):** once warm, the package's headless `session_refresh`
auto-logs-in with the saved Keychain credentials and works *most* of the time (~9s). But
Incapsula fingerprints Playwright-driven navigation and intermittently returns 401 — and
it 401s any Playwright browser regardless of profile warmth (tested). So the model is:

- `scripts/keepalive.py` (run by the heartbeat every 30 min) refreshes before expiry.
- A transient 401 usually clears on the next tick — the GraphQL API calls use plain
  httpx with the cookies (no browser), so they keep working while reese84 is valid.
- If refresh fails repeatedly, the heartbeat sends a macOS notification and the fix is
  re-running `capture_real_session.py` (~2 min). A genuinely human login is the only
  thing Incapsula never blocks; a throwaway profile can't self-re-auth (no HEB password
  stored in it), so it's not a substitute for the capture step.

For max uptime (Phase 5 / Mac mini) the most robust setup is a residential IP plus a
genuine Chrome parked on heb.com that the agent CDP-reads.

## 2. Rotating GraphQL persisted-query hashes (upstream issue #19)

texas-grocery-mcp hardcodes HEB's persisted-query hashes; HEB rotates them on frontend
deploys, breaking cart/store ops with "Persisted query hash … no longer valid". We
don't edit the installed package — instead we override at runtime:

```sh
.venv/bin/python scripts/refresh_graphql_hashes.py   # harvest fresh hashes
```

Writes `config/graphql-hashes.json`; `scripts/shop-server` (the launcher that `.mcp.json`
and the gateway point at) applies them over the package's defaults at startup. Re-run
when cart/store tools start failing with that error. Note `cartItemV2` (add-to-cart)
and other mutation hashes only appear when the action actually fires, so the harvester
performs a real add-to-cart to capture them.

`store_change` (`SelectPickupFulfillment`) is also affected and not worth chasing — the
account default store is used instead; pass `store_id` explicitly (home store: 202).
