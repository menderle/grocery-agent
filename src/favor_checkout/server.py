"""Favor on-demand delivery MCP tools (favor_*). Mounted into grocery-gateway alongside
the HEB tools. REUSES heb_checkout's policy / approvals / audit so spend limits, approval
modes, and the money-safety guards are identical and shared (one weekly cap covers both
HEB and Favor spend). Favor is for fast fill-in orders (≤25 items); HEB stays primary for
stock-ups."""

from fastmcp import FastMCP

from heb_checkout import approvals, audit, config, policy
from . import favor

mcp = FastMCP("favor-checkout")


def _address(address: str | None) -> str:
    addr = address or config.favor_default_address()
    if not addr:
        raise ValueError("No Favor address — pass one or set FAVOR_DEFAULT_ADDRESS in .env.")
    return addr


@mcp.tool
async def favor_search(term: str, address: str | None = None) -> dict:
    """Search the Favor H-E-B Now catalog for on-demand delivery items at the delivery
    address. Use for fast 'I need X in the next hour' requests (≤25 items)."""
    return await favor.search(term, _address(address))


@mcp.tool
async def favor_preview_order(items: list, address: str | None = None,
                              fulfillment: str = "now") -> dict:
    """Build a Favor on-demand cart from `items` and report fee/total/ETA. Never charges.
    `items`: a list of item names (e.g. ["bananas","oat milk"]) or
    [{"name": "...", "quantity": N}]. Confirm ambiguous items via favor_search first.
    Favor's cart is session-bound, so the cart is built fresh in this one call.
    fulfillment: 'now' (~20-45 min) or 'express' (~2h). Cap: 25 items."""
    rec = audit.new_record("favor_preview", fulfillment=fulfillment)
    return await favor.preview(items, _address(address), rec["id"], fulfillment)


@mcp.tool
async def favor_prepare_order(items: list, address: str | None = None,
                              fulfillment: str = "now") -> dict:
    """Build the Favor on-demand cart from `items` and hand off for the user to place.

    Favor requires SMS phone verification at checkout (a fraud gate), so the agent CANNOT
    place a Favor order unattended — only the user, entering the texted code, can. This
    tool does the tedious part: it adds the items to your Favor cart and reaches the
    checkout review, then returns the cart summary + estimated total and tells you to open
    the Favor app/site to place it (one tap + the SMS code). For fully-automated ordering,
    use the HEB tools (scheduled curbside/delivery) instead.
    `items`: list of names (["bananas","oat milk"]) or [{"name":..., "quantity":N}]."""
    rec = audit.new_record("favor_prepare", fulfillment=fulfillment, channel="favor")
    result = await favor.preview(items, _address(address), rec["id"], fulfillment)
    if result.get("status") in (None, "ok") or "estimated_total" in result:
        result["status"] = "cart_ready_place_in_app"
        result["next_step"] = (
            "Your Favor cart is built. Open the Favor app or favordelivery.com and tap "
            "Place Order — Favor will text you a verification code to confirm. (The agent "
            "can't place Favor orders for you because of that SMS step.)")
    return result


@mcp.tool
def favor_status() -> dict:
    """Is Favor configured and ready? (session present, address set, dry-run state)."""
    return {
        "favor_session_present": config.favor_auth_state_path().exists(),
        "default_address": config.favor_default_address() or "(not set — FAVOR_DEFAULT_ADDRESS)",
        "dry_run_mode": config.favor_dry_run_default(),
        "item_cap": favor.ITEM_CAP,
        "setup": "run scripts/start_parked_favor_chrome.sh + sync_parked_favor_session.py; "
                 "set FAVOR_DEFAULT_ADDRESS in .env" if not config.favor_auth_state_path().exists() else "ready",
    }
