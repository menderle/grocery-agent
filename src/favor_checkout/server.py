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
async def favor_preview_order(address: str | None = None, fulfillment: str = "now") -> dict:
    """Walk the current Favor cart to checkout review: fee, total, ETA. Never charges.
    fulfillment: 'now' (~20-45 min) or 'express' (~2h)."""
    rec = audit.new_record("favor_preview", fulfillment=fulfillment)
    return await favor.preview(_address(address), rec["id"], fulfillment)


@mcp.tool
async def favor_place_order(
    expected_total: float,
    address: str | None = None,
    fulfillment: str = "now",
    approval_id: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    """Place the current Favor cart for on-demand delivery. Policy-gated identically to
    HEB place_order (shared spend limits + approval modes). Policy gates (spend limits,
    approval mode, quiet hours) apply REGARDLESS of FAVOR_CHECKOUT_DRY_RUN — dry-run only
    stops the final Place-order click, it does not bypass any check.
    Outcomes: placed | placed_unconfirmed | dry_run | needs_approval | blocked | aborted."""
    try:
        pol = policy.load()
    except Exception as e:
        return {"status": "error", "reason": f"could not load policy: {e}"}

    addr = _address(address)
    approved = False
    approval = None
    if approval_id:
        approval = approvals.consume(approval_id)
        expected_total = approval["order_total"]
        fulfillment = approval.get("slot_text") or fulfillment
        items = items or approval.get("items") or None
        approved = True

    decision = policy.evaluate(expected_total, approved=approved)
    if decision.action == "blocked":
        if approved:
            approvals.restore(approval)
        audit.new_record("blocked", total=expected_total, reason=decision.reason, channel="favor")
        return {"status": "blocked", "reason": decision.reason}
    if decision.action == "needs_approval":
        approval = approvals.create(
            expected_total, "favor:" + fulfillment, fulfillment,
            expiry_hours=pol.get("approval", {}).get("expiry_hours", 4), items=items,
        )
        audit.new_record("pending_approval", total=expected_total, approval_id=approval["id"], channel="favor")
        return {"status": "needs_approval", "approval_id": approval["id"],
                "expires_at": approval["expires_at"], "reason": decision.reason,
                "next_step": "show the user the Favor cart + total + ETA; on a yes, call favor_place_order with this approval_id"}

    dry_run = config.favor_dry_run_default()
    rec = audit.new_record("favor_dry_run" if dry_run else "favor_attempt",
                           total=expected_total, fulfillment=fulfillment, channel="favor")
    try:
        result = await favor.place(addr, rec["id"], fulfillment=fulfillment,
                                   dry_run=dry_run, max_total=round(expected_total * 1.10, 2))
    except Exception:
        if approved:
            approvals.restore(approval)  # favor.place never raises post-click → pre-commit only
        raise

    status = result.get("status")
    if status in ("placed", "placed_unconfirmed"):
        audit.new_record("placed", total=result.get("estimated_total") or expected_total,
                         fulfillment="favor:" + fulfillment, channel="favor",
                         unconfirmed=(status == "placed_unconfirmed"),
                         items=items or [], attempt_id=rec["id"])
    elif status == "aborted" and approved:
        approvals.restore(approval)
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
