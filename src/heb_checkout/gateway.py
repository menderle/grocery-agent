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
    # Same venv as this process unless overridden (Docker, custom layouts).
    return os.environ.get(
        "TEXAS_GROCERY_CMD", str(Path(sys.executable).parent / "texas-grocery-mcp")
    )


def build_gateway() -> FastMCP:
    gateway = FastMCP(
        "grocery-agent",
        instructions=(
            "Grocery agent for HEB. Workflow: search/cart/coupon tools (from the shop "
            "server) to build the cart, then preview_order -> place_order. place_order "
            "is policy-gated (spend limits, approval modes) and may return "
            "needs_approval with an approval_id — show the user the summary and only "
            "retry with that approval_id after an explicit yes. Never attempt to work "
            "around a 'blocked' response: those are the user's own hard limits."
        ),
    )
    from fastmcp.server import create_proxy

    gateway.mount(checkout)
    gateway.mount(
        create_proxy({"mcpServers": {"shop": {"command": _shop_command(), "args": []}}})
    )

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
