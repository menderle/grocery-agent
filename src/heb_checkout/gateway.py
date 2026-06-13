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

import os
import sys
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from . import config
from .server import mcp as checkout


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


def build_gateway() -> FastMCP:
    gateway = FastMCP(
        "grocery-agent",
        instructions=(
            "Grocery agent for HEB.\n\n"
            "CRITICAL store rules:\n"
            "  - The HEB session is ALREADY set to Burnet Rd (store 202) — the cart and "
            "every order go there. This is correct and final.\n"
            "  - ALWAYS pass store_id=\"202\" to EVERY product_search call (without it, "
            "prices come from the wrong store). Never say 'no default store is set' — "
            "just pass store_id=\"202\".\n"
            "  - NEVER call any store-change / set-default-store / set-fulfillment tool. "
            "It fails (a known stale-API issue) AND is unnecessary since the store is "
            "already Burnet Rd. If the user asks to change stores, tell them to set it in "
            "the HEB app/site directly.\n\n"
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
            "the user's own hard limits."
        ),
    )
    from fastmcp.server import create_proxy

    gateway.mount(checkout)
    gateway.mount(
        create_proxy({"mcpServers": {"shop": {"command": _shop_command(), "args": []}}})
    )

    @gateway.custom_route("/list", methods=["POST"])
    async def drop_list(request):
        """Text drop-box: POST plain-text items (one per line) into the inbox file.
        Lets Apple Shortcuts, webhooks, or any other agent feed the grocery list.
        Custom routes bypass MCP auth, so this checks the bearer token itself."""
        from starlette.responses import JSONResponse
        token = os.environ.get("MCP_BEARER_TOKEN", "")
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
            token = os.environ.get("MCP_BEARER_TOKEN")
            if not token or token == "change-me-long-random-string":
                sys.exit("Set a real MCP_BEARER_TOKEN before exposing the HTTP transport.")
            from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
            gateway.auth = StaticTokenVerifier(tokens={token: {"client_id": "grocery-agent"}})
        gateway.run(
            transport="http",
            host="127.0.0.1",
            port=int(os.environ.get("MCP_HTTP_PORT", "8787")),
        )
    else:
        gateway.run()


if __name__ == "__main__":
    main()
