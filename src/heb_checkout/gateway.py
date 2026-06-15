"""Gateway: ONE MCP endpoint exposing the whole grocery agent — texas-grocery-mcp's
shopping tools (proxied over stdio) plus heb-checkout's policy-gated checkout/wallet
tools. This is the composition surface for everything bigger than a single client:

  - hand the agent to another person: they register one server, not two
  - other LLM stacks (any MCP client): one stdio command or one HTTP URL
  - a larger personal-assistant agent: mount THIS gateway under a namespace, e.g.
        assistant.mount(FastMCP.as_proxy("http://host:8787/mcp"), namespace="grocery")
    and the PA gets grocery_* tools alongside its calendar/email/etc.

Run:  grocery-gateway            (stdio)
      grocery-gateway --http     (HTTP on MCP_HTTP_PORT, bearer-token auth, /health)
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from . import config
from .browser import session_live
from .server import _parked_chrome_up, mcp as checkout

try:
    from favor_checkout.server import mcp as favor_mcp
except Exception:  # favor module optional; gateway still works without it
    favor_mcp = None


def _shop_command() -> str:
    # Prefer our launcher (applies config/graphql-hashes.json overrides for HEB's
    # rotating persisted-query hashes); fall back to the bare console script.
    override = os.environ.get("TEXAS_GROCERY_CMD")
    if override:
        return override
    launcher = config.agent_home() / "scripts" / "shop-server"
    if launcher.exists():
        return str(launcher)
    return str(Path(sys.executable).parent / "texas-grocery-mcp")


def _store() -> tuple[str, str]:
    """Home store (id, name) from config/store.json — per-user, not hardcoded."""
    import json
    path = config.agent_home() / "config" / "store.json"
    if path.exists():
        d = json.loads(path.read_text())
        return str(d.get("store_id", "202")), d.get("name", "your H-E-B")
    return "202", "your H-E-B"


def build_gateway() -> FastMCP:
    store_id, store_name = _store()
    gateway = FastMCP(
        "grocery-agent",
        instructions=(
            "Grocery agent for HEB.\n\n"
            "CRITICAL store rules:\n"
            f"  - The HEB session is ALREADY set to {store_name} (store {store_id}) — the "
            "cart and every order go there. This is correct and final.\n"
            f"  - ALWAYS pass store_id=\"{store_id}\" to EVERY product_search call (without "
            "it, prices come from the wrong store). Never say 'no default store is set' — "
            f"just pass store_id=\"{store_id}\".\n"
            "  - NEVER call any store-change / set-default-store / set-fulfillment tool. "
            "It fails (a known stale-API issue) AND is unnecessary since the store is "
            f"already set. If the user asks to change stores, tell them to set it in "
            "the HEB app/site directly.\n\n"
            "MEMORY — learn and reuse the user's usual picks:\n"
            "  - BEFORE searching an item the user names loosely ('water', 'my usual "
            "coffee', 'the 12-pack of water'), call recall_item(phrase). If it returns a "
            "product, add THAT product_id directly — don't re-ask. Call get_preferences "
            "at the start of an order to load brand/size/substitution preferences.\n"
            "  - AFTER the user picks a specific product (or states a preference), call "
            "remember_item(phrase, product_id, sku_id, brand, size) so next time resolves "
            "automatically. Placed orders auto-remember their items too.\n\n"
            "When the user describes a MEAL or EVENT instead of exact items "
            "(e.g. 'hot dogs for 8 people', 'taco night', 'breakfast for the kids'):\n"
            "  1. Work out quantities from the headcount (e.g. ~2 hot dogs/person → "
            "16 → 2 packs of franks + 2 packs of buns). State your assumptions.\n"
            "  2. Ask a SHORT batch of clarifying questions BEFORE building the cart — "
            "the obvious complements and any prefs: buns? condiments (ketchup, mustard, "
            "relish)? sides/chips/drinks? brand or dietary constraints? Ask them "
            "together, not one at a time.\n"
            "  3. After answers, search each item, add to cart, then preview_order and "
            "show the itemized cart + total + pickup/delivery slot.\n\n"
            "Then place_order. It is policy-gated (spend limits, approval modes) and may "
            "return needs_approval with an approval_id — show the summary and only retry "
            "with that approval_id after an explicit yes. Always pass `items` (the cart "
            "contents) to place_order. Never work around a 'blocked' response: those are "
            "the user's own hard limits.\n\n"
            "SESSION HEALTH: if preview_order or place_order returns status 'needs_login', "
            "the HEB session is signed out — tell the user it needs a quick re-login on the "
            "host (use the result's 'recovery' text) and do NOT retry the checkout; it won't "
            "work until they re-login. Nothing was charged.\n\n"
            "FULFILLMENT ROUTING — two delivery paths:\n"
            "  - HEB scheduled (the product_search/cart/place_order tools): curbside or "
            "scheduled home delivery. Default for weekly stock-ups and anything not urgent.\n"
            "  - FAVOR on-demand (favor_search, favor_prepare_order): ~20-45 min / ~2h, "
            "up to 25 items, for URGENCY ('in the next hour', 'ran out of X'). NOTE: Favor "
            "requires SMS verification at checkout, so the agent builds the cart and the "
            "USER places it in the Favor app (favor_prepare_order returns the hand-off). "
            "The agent CANNOT place a Favor order itself.\n"
            "Pick based on intent; if ambiguous, ask 'scheduled (HEB) or on-demand (Favor)?'."
        ),
    )
    from fastmcp.server import create_proxy

    gateway.mount(checkout)
    if favor_mcp is not None:
        gateway.mount(favor_mcp)  # favor_* on-demand delivery tools
    gateway.mount(
        create_proxy({"mcpServers": {"shop": {"command": _shop_command(), "args": []}}})
    )

    @gateway.custom_route("/list", methods=["POST"])
    async def drop_list(request):
        """Text drop-box: POST plain-text items (one per line) into the inbox file.
        Lets Apple Shortcuts, webhooks, or any other agent feed the grocery list.
        Uses a static LIST_DROP_TOKEN (separate from MCP/OAuth) because Shortcuts can't
        do an OAuth flow; this route never touches checkout, so a static secret is fine."""
        from starlette.responses import JSONResponse
        token = os.environ.get("LIST_DROP_TOKEN", "")
        sent = request.headers.get("authorization", "")
        if not token or sent != f"Bearer {token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = (await request.body()).decode("utf-8", errors="replace")
        from . import lists
        added = lists.append_inbox(body)
        return JSONResponse({"added": added})

    @gateway.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        auth = config.auth_state_path()
        return JSONResponse({
            "ok": auth.exists(),
            "session_file_exists": auth.exists(),
            "session_file_age_hours": round(
                (datetime.now().timestamp() - auth.stat().st_mtime) / 3600, 1
            ) if auth.exists() else None,
            "session_authenticated": session_live(),  # durable login cookies valid, not just file present
            "parked_chrome_up": await asyncio.to_thread(_parked_chrome_up),  # probe off-loop (≤1s)
            "dry_run_mode": config.dry_run_default(),
        })

    return gateway


def main() -> None:
    gateway = build_gateway()
    if "--http" in sys.argv:
        no_auth = os.environ.get("MCP_ALLOW_NO_AUTH", "").lower() in ("1", "true", "yes")
        if no_auth:
            # Open endpoint — ONLY acceptable while HEB_CHECKOUT_DRY_RUN=true (no charge
            # possible). claude.ai connectors don't accept a static bearer token, so this
            # is the temporary path for testing; lock down with OAuth before real orders.
            if not config.dry_run_default():
                sys.exit("Refusing to run no-auth with dry-run OFF — that exposes real "
                         "ordering. Set up OAuth or keep HEB_CHECKOUT_DRY_RUN=true.")
        else:
            # OAuth: Google login restricted to OAUTH_ALLOWED_EMAILS. This is what
            # claude.ai connectors authenticate against (no static-token field exists).
            from .auth import allowed_emails, build_google_auth, OwnerOnly
            gateway.auth = build_google_auth()
            gateway.add_middleware(OwnerOnly(allowed_emails()))
        gateway.run(
            transport="http",
            host="127.0.0.1",
            port=int(os.environ.get("MCP_HTTP_PORT", "8787")),
        )
    else:
        gateway.run()


if __name__ == "__main__":
    main()
