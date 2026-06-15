"""heb-checkout MCP server.

Tools cover the checkout half of the grocery agent (texas-grocery-mcp owns
search/cart/coupons). Policy is enforced in code in place_order; approval mode,
spend limits, quiet hours, and order frequency cannot be bypassed by prompting.

Transport: stdio only (local Claude Code / Claude Desktop, or mounted inside
grocery-gateway). The public, OAuth-secured HTTP endpoint is `grocery-gateway --http`.
"""

import os
import sys
from datetime import datetime

from fastmcp import FastMCP

import hashlib

from . import approvals, audit, checkout_driver, config, policy, preferences
from .checkout_driver import parse_dollars
from .locking import async_checkout_lock

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
    expected_total: float | None = None,
    fulfillment: str = "pickup",
    slot_text: str | None = None,
    approval_id: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """Place the order for the current HEB cart. expected_total: the order total from
    preview_order (policy evaluates against it, and checkout aborts if the on-screen
    total comes out >10% higher) — REQUIRED unless approval_id is given (an approval
    carries its own total). slot_text: substring of the chosen slot from get_slots.
    approval_id: pass when the user has approved a pending order (its stored total,
    fulfillment, slot, and items govern). items: the cart contents as
    [{"name": ..., "quantity": ...}] — ALWAYS pass these (from the cart tools) so
    purchase history powers suggest_replenishment.

    Outcomes: placed | dry_run | needs_approval (returns approval_id to show the
    user) | blocked (policy; not overridable) | aborted (total mismatch) |
    duplicate_skipped (an identical order was just placed)."""
    # Load policy BEFORE consuming the approval — a missing/broken policy.yaml must not
    # pop (burn) the user's approval while never placing the order.
    try:
        pol = policy.load()
    except Exception as e:
        return {"status": "error", "reason": f"could not load policy: {e}"}

    # expected_total is required for a fresh order; an approval carries its own total.
    if not approval_id and (expected_total is None or expected_total <= 0):
        return {"status": "error",
                "reason": "expected_total (from preview_order) is required when no approval_id is given"}

    # One cross-process lock around the whole critical section (approval consume →
    # checkout → audit) so the web UI and the Claude connector can't double-place or
    # both consume one approval. Acquired off the event loop (async_checkout_lock), so a
    # contending checkout waits without freezing the web server. Money-safety semantics
    # below are unchanged.
    async with async_checkout_lock():
        approved = False
        approval = None
        if approval_id:
            approval = approvals.consume_locked(approval_id)  # lock already held
            expected_total = approval["order_total"]
            fulfillment = approval["fulfillment"]
            slot_text = approval.get("slot_text") or slot_text
            items = items or approval.get("items") or None
            approved = True

        decision = policy.evaluate(expected_total, approved=approved)
        if decision.action == "blocked":
            if approved:
                approvals.restore(approval)  # a policy block isn't a 'no' — keep the yes usable
            audit.new_record("blocked", total=expected_total, reason=decision.reason)
            return {"status": "blocked", "reason": decision.reason}
        if decision.action == "needs_approval":
            approval = approvals.create(
                expected_total, fulfillment, slot_text,
                expiry_hours=pol.get("approval", {}).get("expiry_hours", 4),
                items=items,
            )
            audit.new_record("pending_approval", total=expected_total, approval_id=approval["id"])
            return {
                "status": "needs_approval",
                "approval_id": approval["id"],
                "order_total": expected_total,
                "items": items or [],
                "fulfillment": fulfillment,
                "slot_text": slot_text,
                "expires_at": approval["expires_at"],
                "reason": decision.reason,
                "next_step": "show the user the itemized cart, total, and slot; on a yes, call place_order with this approval_id",
            }

        dry_run = config.dry_run_default()
        # Idempotency: for REAL orders, refuse to re-place an identical cart that was just
        # placed (a confused retry, or a web+connector race that the lock serialized but
        # would otherwise double-charge). Dry-runs always rehearse.
        fingerprint = _cart_fingerprint(expected_total, items)
        if not dry_run:
            dup = _recent_placed(fingerprint, window_seconds=900)
            if dup is not None:
                return {"status": "duplicate_skipped",
                        "reason": "an identical order was placed moments ago — not charging again",
                        "confirmation": dup.get("confirmation"),
                        "placed_at": dup.get("placed_at")}
        rec = audit.new_record("dry_run" if dry_run else "attempt", total=expected_total,
                               fulfillment=fulfillment, slot=slot_text, reason=decision.reason)
        try:
            result = await checkout_driver.place(
                fulfillment, slot_text, rec["id"],
                dry_run=dry_run, max_total=round(expected_total * 1.10, 2),
            )
        except Exception:
            # place() never raises AFTER the commit click, so any exception here is
            # pre-commit and safe to restore the approval for a retry.
            if approved:
                approvals.restore(approval)
            raise

        status = result.get("status")
        if status in ("placed", "placed_unconfirmed"):
            # Money has moved (or very likely has). Record it (counts toward spend limits)
            # and do NOT restore the approval — never auto-retry a committed order.
            final_total = result.get("estimated_total") or expected_total
            audit.new_record("placed", total=final_total, fulfillment=fulfillment,
                             slot=slot_text, confirmation=result.get("confirmation"),
                             unconfirmed=(status == "placed_unconfirmed"),
                             items=items or [], attempt_id=rec["id"],
                             fingerprint=fingerprint)
            _learn_items(items)  # remember what was bought (never affects the outcome)
        elif status == "aborted" and approved:
            # Pre-commit abort (total mismatch / Place-order disabled) — keep the yes usable.
            approvals.restore(approval)
        return result


def _cart_fingerprint(expected_total, items: list[dict] | None) -> str:
    """Stable hash of (rounded total + normalized item set) to detect a duplicate order."""
    norm = sorted(
        (str(i.get("name", "")).strip().lower(), i.get("quantity"))
        for i in (items or []) if isinstance(i, dict)
    )
    raw = json.dumps([round(float(expected_total or 0), 2), norm], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _recent_placed(fingerprint: str, window_seconds: int) -> dict | None:
    """Most recent placed order with this fingerprint within the window, else None."""
    now = datetime.now()
    for r in reversed(audit.placed_orders()):
        if r.get("fingerprint") != fingerprint:
            continue
        try:
            ts = datetime.fromisoformat(r["placed_at"])
        except (KeyError, TypeError, ValueError):
            continue
        return r if (now - ts).total_seconds() <= window_seconds else None
    return None


def _learn_items(items: list[dict] | None) -> None:
    """After a successful order, remember each item so 'add my usual X' recalls it later.
    Best-effort: a memory write must never alter or fail the checkout outcome, and it must
    NOT overwrite a value the user has curated for a colliding phrase (overwrite=False)."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        try:
            preferences.remember(
                name, overwrite=False, display_name=name, quantity=item.get("quantity"),
                product_id=item.get("product_id"), sku_id=item.get("sku_id"),
            )
        except Exception:
            pass


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


@mcp.tool
def remember_item(
    phrase: str,
    product_id: str | None = None,
    sku_id: str | None = None,
    brand: str | None = None,
    size: str | None = None,
    quantity: int | None = None,
    substitution: str | None = None,
    display_name: str | None = None,
    note: str | None = None,
) -> dict:
    """Save the user's preferred product for a phrase so it's remembered next time.
    Call this whenever the user states or confirms a preference — e.g. they say "my
    water is the H-E-B 1877 12-pack" or they pick a specific result after you searched.
    `phrase` is how the user refers to it ("water", "my usual coffee"); pass the
    product_id/sku_id from the search result plus brand/size when you have them. Fields
    merge, so you can add detail later without losing the saved SKU."""
    entry = preferences.remember(
        phrase, product_id=product_id, sku_id=sku_id, brand=brand, size=size,
        quantity=quantity, substitution=substitution, display_name=display_name, note=note,
    )
    return {"remembered": entry}


@mcp.tool
def recall_item(phrase: str) -> dict:
    """Look up the user's saved product for a phrase BEFORE searching, so ambiguous
    requests ("add my usual water", "the 12-pack of water") resolve to the exact product
    without re-asking. Returns the remembered product (display_name, product_id, sku_id,
    brand, size, quantity, substitution) or {"found": false}. If found, add that
    product_id directly; if not, search and then offer to remember_item the user's pick."""
    entry = preferences.resolve(phrase)
    return {"found": True, "item": entry} if entry else {"found": False}


@mcp.tool
def forget_item(phrase: str) -> dict:
    """Forget a previously remembered product for a phrase (the user changed their mind
    about their 'usual')."""
    return {"forgotten": preferences.forget(phrase)}


@mcp.tool
def get_preferences() -> dict:
    """The user's durable preferences and product memory: general settings (default
    substitution, brand notes, avoid list, notes), every remembered phrase→product
    mapping, and the standing staples list. Read this when building an order so picks
    match what the user has chosen before."""
    return {
        "general": preferences.general(),
        "items": preferences.all_items(),
        "staples": preferences.staples(),
    }


@mcp.tool
def add_staple(query: str, quantity: int = 1, substitution: str = "ask") -> dict:
    """Add (or update) an item in the standing weekly order. Use when the user says
    something like 'always order oat milk' or after they confirm a repeatedly-added item
    should become a staple."""
    return {"staples": preferences.add_staple(query, quantity, substitution)}


@mcp.tool
def remove_staple(query: str) -> dict:
    """Remove an item from the standing weekly order."""
    return {"staples": preferences.remove_staple(query)}


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
    # heb-checkout is the checkout half only, used over stdio (local Claude Code/Desktop)
    # or mounted inside grocery-gateway. The PUBLIC, OAuth-secured HTTP endpoint is
    # grocery-gateway --http — keep one auth path, don't expose this server directly.
    if "--http" in sys.argv:
        sys.exit("Run `grocery-gateway --http` for the public OAuth-secured endpoint; "
                 "heb-checkout runs over stdio only.")
    mcp.run()


if __name__ == "__main__":
    main()
