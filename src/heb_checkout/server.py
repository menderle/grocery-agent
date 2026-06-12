"""heb-checkout MCP server.

Tools cover the checkout half of the grocery agent (texas-grocery-mcp owns
search/cart/coupons). Policy is enforced in code in place_order; approval mode,
spend limits, quiet hours, and order frequency cannot be bypassed by prompting.

Transports:
  stdio (default)         — local Claude Code / Claude Desktop
  --http                  — remote access (phone via Cloudflare tunnel); requires
                            MCP_BEARER_TOKEN, serves /health for the heartbeat.
"""

import os
import sys
from datetime import datetime

from fastmcp import FastMCP

from . import approvals, audit, checkout_driver, config, policy
from .checkout_driver import parse_dollars

mcp = FastMCP("heb-checkout")


@mcp.tool
def get_policy() -> dict:
    """Current purchase policy: autonomy mode, spend limits, fulfillment default, plus
    rolling spend totals and any pending approvals."""
    history = audit.placed_orders()
    now = datetime.now()

    def total_since(days: int) -> float:
        from datetime import timedelta
        cutoff = now - timedelta(days=days)
        return sum(o["total"] for o in history if datetime.fromisoformat(o["placed_at"]) >= cutoff)

    return {
        "policy": policy.load(),
        "spent_last_7_days": round(total_since(7), 2),
        "spent_last_30_days": round(total_since(30), 2),
        "pending_approvals": approvals.pending(),
    }


@mcp.tool
def set_policy(field: str, value: str) -> dict:
    """Update a policy setting at the user's request. Settable fields: 'mode'
    (approve|auto_under_threshold|full_auto), 'auto_threshold', 'fulfillment'
    (pickup|delivery|ask), 'spend_limits.per_order', 'spend_limits.weekly',
    'spend_limits.monthly'. Only change these when the user explicitly asks."""
    updated = policy.update(field, value)
    return {"updated": field, "policy": updated}


@mcp.tool
async def get_slots(fulfillment: str = "both") -> dict:
    """Available HEB time slots. fulfillment: 'pickup', 'delivery', or 'both'
    (side-by-side, for suggesting times against the user's calendar)."""
    kinds = ["pickup", "delivery"] if fulfillment == "both" else [fulfillment]
    out = {}
    for kind in kinds:
        try:
            out[kind] = await checkout_driver.get_slots(kind)
        except Exception as e:  # one fulfillment type failing shouldn't hide the other
            out[kind] = {"error": str(e)}
    return out


@mcp.tool
async def preview_order(fulfillment: str = "pickup") -> dict:
    """Walk the current cart to the final review screen and report itemized total,
    fees, and the saved payment method. Never places an order, never charges."""
    rec = audit.new_record("preview", fulfillment=fulfillment)
    return await checkout_driver.preview(fulfillment, rec["id"])


@mcp.tool
async def place_order(
    expected_total: float,
    fulfillment: str = "pickup",
    slot_text: str | None = None,
    approval_id: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """Place the order for the current HEB cart. expected_total: the order total from
    preview_order (policy evaluates against it, and checkout aborts if the on-screen
    total comes out >10% higher). slot_text: substring of the chosen slot from
    get_slots. approval_id: pass when the user has approved a pending order.
    items: the cart contents as [{"name": ..., "quantity": ...}] — ALWAYS pass these
    (from the cart tools) so purchase history powers suggest_replenishment.

    Outcomes: placed | dry_run | needs_approval (returns approval_id to show the
    user) | blocked (policy; not overridable) | aborted (total mismatch)."""
    approved = False
    if approval_id:
        approval = approvals.consume(approval_id)  # raises if expired/unknown
        expected_total = approval["order_total"]
        fulfillment = approval["fulfillment"]
        slot_text = approval.get("slot_text") or slot_text
        items = items or approval.get("items") or None
        approved = True

    decision = policy.evaluate(expected_total, approved=approved)
    if decision.action == "blocked":
        audit.new_record("blocked", total=expected_total, reason=decision.reason)
        return {"status": "blocked", "reason": decision.reason}
    if decision.action == "needs_approval":
        pol = policy.load()
        approval = approvals.create(
            expected_total, fulfillment, slot_text,
            expiry_hours=pol.get("approval", {}).get("expiry_hours", 4),
            items=items,
        )
        audit.new_record("pending_approval", total=expected_total, approval_id=approval["id"])
        return {
            "status": "needs_approval",
            "approval_id": approval["id"],
            "expires_at": approval["expires_at"],
            "reason": decision.reason,
            "next_step": "show the user the cart summary and total; on a yes, call place_order with this approval_id",
        }

    dry_run = config.dry_run_default()
    rec = audit.new_record("dry_run" if dry_run else "attempt", total=expected_total,
                           fulfillment=fulfillment, slot=slot_text, reason=decision.reason)
    try:
        result = await checkout_driver.place(
            fulfillment, slot_text, rec["id"],
            dry_run=dry_run, max_total=round(expected_total * 1.10, 2),
        )
    except Exception:
        if approved:
            approvals.restore(approval)  # technical failure shouldn't burn the user's yes
        raise
    if result.get("status") == "placed":
        final_total = parse_dollars(result.get("order_total")) or expected_total
        audit.new_record("placed", total=final_total, fulfillment=fulfillment,
                         slot=slot_text, confirmation=result.get("confirmation"),
                         items=items or [], attempt_id=rec["id"])
    return result


@mcp.tool
def order_history(limit: int = 10) -> dict:
    """Recent checkout activity from the audit log (placed orders, dry runs, blocks)."""
    return {"records": audit.all_records()[-limit:]}


@mcp.tool
async def list_payment_methods() -> dict:
    """Saved payment methods in the HEB wallet (last-4 only)."""
    from . import wallet
    return await wallet.list_methods()


@mcp.tool
async def update_payment_card(
    card_number: str | None = None,
    expiry: str | None = None,
    cvv: str | None = None,
    name_on_card: str | None = None,
    billing_zip: str | None = None,
    set_default: bool = True,
    remove_old: bool = True,
) -> dict:
    """Save a new card in the HEB wallet, set it default, and remove the old card(s).

    Preferred: call with NO card arguments — the card is read from the local vault
    (user ran scripts/add_card.py) and the vault entry is deleted after a successful
    save. Card details passed as arguments work too, but remind the user they then
    persist in the chat transcript. Prepaid gift cards MUST be registered with
    name+ZIP at the issuer first or HEB declines them (AVS)."""
    from . import cards, wallet

    if card_number:
        card = {"number": card_number, "expiry": expiry or "", "cvv": cvv or "",
                "name": name_on_card or "", "zip": billing_zip or ""}
        if not cards.luhn_ok("".join(c for c in card_number if c.isdigit())):
            return {"status": "error", "reason": "card number failed checksum — check for typos"}
        source = "chat"
    else:
        card = cards.fetch()
        if card is None:
            return {
                "status": "no_pending_card",
                "next_step": "ask the user to run: .venv/bin/python scripts/add_card.py "
                             "(or to provide the card details directly, noting transcript persistence)",
            }
        source = "vault"

    result = await wallet.add_card(card, set_default=set_default, remove_old=remove_old)
    if source == "vault":
        cards.delete()  # secret's job is done
    audit.new_record("card_update", source=source, **result)  # last4-only by construction
    return {**result, "card_source": source, "vault_cleared": source == "vault"}


@mcp.tool
async def remove_payment_card(card_last4: str) -> dict:
    """Remove a saved card (by its last 4 digits) from the HEB wallet."""
    from . import wallet
    result = await wallet.remove_card(card_last4)
    audit.new_record("card_removed", **result)
    return result


@mcp.tool
def read_grocery_lists() -> dict:
    """Gather grocery items the user has noted everywhere: Apple Notes ('Groceries'
    note), Apple Reminders ('Groceries' list — Siri adds land here), a link-shared
    Google Doc/Sheet, and the inbox file (fed by file sync or the POST /list
    endpoint). Returns per-source items plus a deduplicated merged list. Sources are
    configured in config/lists.yaml; unavailable sources report a hint, never fail."""
    from . import lists
    return lists.read_all()


@mcp.tool
def clear_grocery_list(source: str, items: list[str]) -> dict:
    """Mark list items handled AFTER they made it into a placed (or user-confirmed)
    order: completes Reminders, checks off Notes lines (✓), empties the inbox file.
    source: apple_notes | apple_reminders | inbox_file. Google Docs are read-only."""
    from . import lists
    return lists.clear(source, items)


@mcp.tool
def get_upcoming_events(days: int = 7) -> dict:
    """Upcoming calendar events from the user's ICS feeds (GROCERY_ICS_URLS in .env).
    Use when building an order: events that involve hosting, cooking, parties, trips,
    or holidays are opportunities to SUGGEST extra groceries — propose, never add
    without the user's yes. A trip spanning the order window may also mean ordering
    less or shifting the delivery slot."""
    from . import calendar_events
    return calendar_events.upcoming_events(days)


@mcp.tool
def suggest_replenishment(horizon_days: int = 7) -> dict:
    """Predict what the user is running low on, from their actual purchase cycles
    (median days between repeat purchases in placed-order history). Run when building
    the weekly order; propose due/overdue items alongside the staples list. Items
    need >=2 recorded purchases before they get predictions."""
    from . import replenishment
    return replenishment.suggest(horizon_days)


@mcp.tool
def check_upstream_updates() -> dict:
    """Compare the installed texas-grocery-mcp version against the latest PyPI release.
    Reports only — the version is pinned in pyproject.toml and never changes without a
    deliberate pin bump. Run when the user asks about MCP updates."""
    from . import updates
    return updates.check()


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse
    auth = config.auth_state_path()
    return JSONResponse({
        "ok": auth.exists(),
        "heb_session_file": str(auth),
        "session_file_exists": auth.exists(),
        "session_file_age_hours": round((datetime.now().timestamp() - auth.stat().st_mtime) / 3600, 1)
        if auth.exists() else None,
        "dry_run_mode": config.dry_run_default(),
    })


def main() -> None:
    if "--http" in sys.argv:
        token = os.environ.get("MCP_BEARER_TOKEN")
        if not token or token == "change-me-long-random-string":
            sys.exit("Set a real MCP_BEARER_TOKEN before exposing the HTTP transport.")
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        mcp.auth = StaticTokenVerifier(tokens={token: {"client_id": "grocery-agent"}})
        mcp.run(
            transport="http",
            host="127.0.0.1",  # only the Cloudflare tunnel reaches this
            port=int(os.environ.get("MCP_HTTP_PORT", "8787")),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
