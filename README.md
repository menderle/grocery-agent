# HEB Grocery Agent

Always-on agent that buys groceries from HEB: shop by chatting with Claude (incl. phone),
a shared Reminders list, email, or a weekly standing order. Pays with a prepaid debit card
saved on the HEB account. Checkout autonomy is a policy toggle, enforced in code.

Ordering preference: **HEB natively** (curbside pickup + HEB scheduled delivery — what this
repo implements) with **Favor** (H-E-B-owned, on-demand ~2h delivery) as the planned fast-
delivery module. Third-party marketplaces (Instacart etc.) are deliberately out of scope.

Full design: `~/.claude/plans/i-wnat-to-create-synchronous-otter.md`

## Pieces

| Piece | What it does |
|---|---|
| [texas-grocery-mcp](https://github.com/mgwalkerjr95/texas-grocery-mcp) | HEB search, cart, coupons, store selection, session refresh — a **PyPI dependency pinned at 0.1.3** in pyproject.toml (installed into the gitignored `.venv/`, never vendored into this repo, never fetched from GitHub at runtime) |
| `src/heb_checkout/` | Custom MCP: `get_slots`, `preview_order`, `place_order` (dry-run capable), `get_policy`/`set_policy`, `order_history`, wallet management (`update_payment_card`, `list_payment_methods`, `remove_payment_card`), `check_upstream_updates`, `/health` |
| `config/policy.yaml` | Autonomy mode, spend limits, quiet hours, fulfillment default |
| `config/lists.yaml` + `src/heb_checkout/lists.py` | List intake: Apple Notes, Apple Reminders (Siri), link-shared Google Doc/Sheet, iMessage (opt-in), Todoist, Notion, inbox file + authenticated `POST /list` drop endpoint |
| `calendar_events.py`, `replenishment.py` | Smart suggestions: ICS-feed calendar awareness (party → propose supplies) and purchase-cycle replenishment ("milk due in 2 days") |
| `data/` | Staples, preferences, append-only order audit log |
| `deploy/`, `Dockerfile`, `Makefile` | launchd services, heartbeat, Docker stack, host migration |

**→ Step-by-step setup: [SETUP.md](SETUP.md)** (written for a store-bought prepaid
Mastercard — registration for AVS is mandatory before heb.com will accept it).

## New machine / new person? One command

```sh
git clone <this repo> grocery-agent && cd grocery-agent && zsh scripts/install.sh
```

Installs Python/deps/Chromium, generates secrets and MCP registration for that machine,
and proves the safety layer with a selftest. Works with **any MCP-capable LLM client**,
not just Claude — and `grocery-gateway` exposes the whole agent as one server that a
larger personal-assistant agent can mount as its "grocery" capability. See
[docs/INTEGRATION.md](docs/INTEGRATION.md) and [prompts/system-prompt.md](prompts/system-prompt.md).

## Setup — manual steps (Maurice)

1. **HEB account (Phase 0):** create/verify account on heb.com, set home store, place one
   manual curbside order to learn the flow.
2. **Card:** create a [Privacy.com](https://privacy.com) virtual card, merchant-locked to
   H-E-B with a monthly limit (or a registered reloadable prepaid Visa/MC — must have your
   name + billing address on file or AVS declines). Save it **once** as the default payment
   method on heb.com. Card numbers never enter this repo, the agent, or its logs.
3. **First login:** in Claude (this project), ask to "authenticate with HEB" — the
   texas-grocery MCP opens a browser for login and stores the session at
   `~/.texas-grocery-mcp/auth.json` (shared with checkout). Credentials go in the macOS keyring.
4. **Secrets:** `cp config/.env.example .env`, set a long random `MCP_BEARER_TOKEN`.
5. **Phone access:** create a Cloudflare tunnel (`cloudflared tunnel create grocery-agent`)
   pointing at `http://127.0.0.1:8787`, then add `https://<tunnel-host>/mcp` as a custom
   connector on claude.ai with the bearer token → orders from the Claude phone app.
6. **Always-on (this Mac):** `make install-launchd` (MCP server + 30-min heartbeat that
   notifies if the agent is down). Keep the Mac plugged in; Phase 5 of the plan moves this
   to a Mac mini with `make migrate`.

## Safety model

- `place_order` is the **only** code path that can spend money. It consults
  `config/policy.yaml` + the audit log before every order: mode
  (`approve` / `auto_under_threshold` / `full_auto`), per-order / rolling weekly / monthly
  spend limits (hard blocks, not overridable by approval), max orders per day, quiet hours.
- `HEB_CHECKOUT_DRY_RUN=true` (current default everywhere) stops one click before purchase
  and screenshots the final screen. Flip to `false` only after Phase 2 verification.
- Live orders abort if the on-screen total exceeds the policy-evaluated total by >10%.
- Every attempt (placed / dry-run / blocked / pending) is an audit record in `data/orders/`.
- **Card handling:** the agent manages the HEB wallet (add/swap/remove cards), but full
  card numbers stay out of logs and audit records (last-4 only). Preferred intake is the
  local keyring vault (`scripts/add_card.py` → "switch HEB to my new card" → vault entry
  deleted after save); chat-provided details are supported but persist in the transcript.
- Final backstop independent of all software: the prepaid card's own balance/limit.

Change behavior by talking to the agent: "switch to full auto", "set my weekly limit to
$250", "make this one delivery" — these edit `policy.yaml` via `set_policy` (allow-listed
fields only).

## Verify

```sh
make selftest        # policy engine, audit, approvals — no network
make serve-http &    # then: curl localhost:8787/health
```

Phase 2 (after first HEB login): run `preview_order` and dry-run `place_order` for both
pickup and delivery, review screenshots in `data/orders/screenshots/`, fix
`SELECTORS` in `src/heb_checkout/checkout_driver.py` against the real DOM — they are
best-effort until verified — then one watched live order (~$20) in `approve` mode.

## Status

- [x] Scaffold, venv (Python 3.12 via uv), texas-grocery-mcp 0.1.3 + Chromium installed
- [x] heb-checkout MCP: policy engine (14-check selftest), approvals, audit, HTTP+bearer, /health
- [x] Portability: Dockerfile/compose, snapshot/restore/migrate, launchd + heartbeat
- [ ] Phase 0: HEB account + prepaid card (manual)
- [ ] Phase 2 verification: selectors against live logged-in checkout, first live order
- [ ] Phase 4: tunnel + phone connector, Reminders/email sweeps, weekly standing order
- [ ] Later: `favor_checkout` driver for on-demand delivery — same pattern as the HEB driver
      (Playwright against favordelivery.com web ordering, no public API exists, prepaid card
      saved on the Favor account, same policy engine gates `place_order`)
