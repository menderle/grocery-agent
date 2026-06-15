"""Drives heb.com checkout with Playwright. Flow verified against the live site
(2026-06-12), curbside:

  cart → "Choose pickup time" → expand "Scheduled" → pick a slot → "Select this time"
       → "Start checkout" → /precheckout upsell → "Continue"
       → /checkout → select saved payment card → "Place order" (enabled only after
         a card is chosen).

dry_run=True stops one click before "Place order" and screenshots the final screen —
the ONLY function that can spend money is place(dry_run=False). Selectors live in
SELECTORS so a UI change is a one-place fix.
"""

import re

from playwright.async_api import Page

from . import audit
from .browser import SessionExpiredError, heb_page, human_pause

CART_URL = "https://www.heb.com/cart"

# If heb.com bounces an unauthenticated request to a sign-in PATH, the session is dead.
# Anchored to path segments so a stray 'signin' in a query string can't false-trigger an abort.
_LOGIN_URL_MARKERS = ("/login", "/signin", "/sign-in", "/account/sign")


async def _assert_logged_in(page: Page) -> None:
    """After landing on the cart, if HEB redirected us to a sign-in page the session is
    expired — fail fast with a typed error so the caller surfaces 'needs_login' rather than
    timing out deep in the walk on a control that will never render."""
    url = (page.url or "").lower()
    if any(m in url for m in _LOGIN_URL_MARKERS):
        raise SessionExpiredError("heb.com redirected to sign-in — HEB session expired")

SELECTORS = {
    "choose_time": "button:has-text('Choose pickup time'), button:has-text('Choose delivery time'), button:has-text('Change time')",
    "fulfillment_tab": "button[role='tab'], [role='tablist'] button",  # Curbside / Delivery
    "scheduled_expander": "button[aria-label*='Scheduled time slots'], button[aria-label*='View Scheduled'], button:has-text('Scheduled')",
    "select_time": "button:has-text('Select this time'), button:has-text('Reserve this time')",
    # HEB uses data-qe-id="placeOrderButton" for the submit on BOTH cart ('Start checkout')
    # and /checkout ('Place order') — disambiguate by label. The cart also exposes a
    # 'footerStartCheckout' variant.
    "start_checkout": "button[data-qe-id='placeOrderButton']:has-text('Start checkout'), button[data-qe-id='footerStartCheckout'], button:has-text('Start checkout')",
    "precheckout_continue": "a:has-text('Continue'), button:has-text('Continue')",
    "place_order": "button[data-qe-id='placeOrderButton']:has-text('Place order'), button:has-text('Place order')",
}


def parse_dollars(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\$\s*([\d,]+\.?\d*)", text)
    return float(m.group(1).replace(",", "")) if m else None


async def _dismiss_modals(page: Page) -> None:
    """Close interstitial popups ('What's New', promos) that intercept clicks."""
    for _ in range(3):
        cover = page.locator("[data-component='modal-cover-container']").first
        if not await cover.count():
            return
        close = page.locator(
            "[data-component*='modal'] button[aria-label*='lose'], "
            "[data-component*='modal'] button:has-text('Close'), "
            "[data-component*='modal'] button:has-text('Got it')"
        ).first
        if await close.count():
            await close.click()
        else:
            await page.keyboard.press("Escape")
        await human_pause(0.8, 1.5)


async def _shot(page: Page, order_id: str, name: str) -> str:
    path = audit.screenshots_dir(order_id) / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def _estimated_total(page: Page) -> float | None:
    body = " ".join((await page.locator("body").inner_text()).split())
    m = re.search(r"Estimated total \$([\d,]+\.?\d*)", body)
    return float(m.group(1).replace(",", "")) if m else None


async def _open_time_chooser(page: Page, fulfillment: str) -> None:
    """Open the fulfillment dialog and switch tab if needed (curbside is default)."""
    btn = page.locator(SELECTORS["choose_time"]).first
    if not await btn.count():
        raise RuntimeError(
            "no 'Choose pickup time' control on the cart — cart may be empty or the "
            "layout changed (fix SELECTORS['choose_time'])"
        )
    await btn.click()
    await human_pause(1.5, 3.0)
    if fulfillment == "delivery":
        tab = page.locator(SELECTORS["fulfillment_tab"], has_text="Delivery").first
        if await tab.count():
            await tab.click()
            await human_pause()
    sched = page.locator(SELECTORS["scheduled_expander"]).first
    if await sched.count():
        await sched.click()
        await human_pause()


async def _dialog_text(page: Page) -> str:
    """Normalized text of the open reserve dialog (or the page if no modal is up)."""
    dlg = page.locator("[role='dialog']").first
    scope = dlg if await dlg.count() else page
    return " ".join((await scope.inner_text()).split())


# Reserve-dialog token scan: time ranges sit under headers that are either a price ("Under 2
# hours $7.95" = paid express) or "Free" ("Afternoon Free" = free pickup). Tag each range by
# the most recent header so the walk defaults to a FREE slot — a paid express fee would push the
# checkout total past the approved amount and trip the >10% abort.
_RANGE = r"\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}\s*[AP]M"
_TOKEN_RE = re.compile(r"\$\d+\.\d{2}|\bFree\b|" + _RANGE)


def _slots_by_price(text: str) -> tuple[list[str], list[str]]:
    free: list[str] = []
    paid: list[str] = []
    paid_ctx = False
    seen: set[tuple[bool, str]] = set()
    for tok in _TOKEN_RE.findall(text):
        tok = tok.strip()
        if tok.startswith("$"):
            paid_ctx = True
        elif tok == "Free":
            paid_ctx = False
        else:
            key = (paid_ctx, tok)
            if key not in seen:  # dedupe per-bucket so a free time isn't lost to a same-clock paid one
                seen.add(key)
                (paid if paid_ctx else free).append(tok)
    return free, paid


async def get_slots(fulfillment: str, headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await _assert_logged_in(page)
        await page.wait_for_timeout(6000)  # cart fulfillment controls render async
        await _open_time_chooser(page, fulfillment)
        free, paid = _slots_by_price(await _dialog_text(page))
        return {"fulfillment": fulfillment, "free_slots": free, "paid_slots": paid,
                "slots": free + paid, "slot_count": len(free) + len(paid)}


async def _reserve_and_advance(page: Page, fulfillment: str, slot_text: str | None,
                               order_id: str) -> None:
    """Cart → reserve a slot (unless one is already reserved) → Start checkout →
    past the precheckout upsell."""
    await _dismiss_modals(page)  # 'What's New' covers intercept every click
    # A slot is reserved iff the time control reads 'Change time'. While it still reads
    # 'Choose pickup/delivery time', NO slot is reserved — and the Start-checkout button is
    # present on the cart REGARDLESS of reservation, so its presence is NOT a reliable signal
    # (gating on it skipped reservation entirely and stalled the walk on the cart).
    reserved = await page.locator("button:has-text('Change time')").first.count() > 0
    unreserved = await page.locator(
        "button:has-text('Choose pickup time'), button:has-text('Choose delivery time')"
    ).first.count() > 0
    if not reserved and not unreserved:
        raise RuntimeError("the cart's time control didn't render — can't tell if a slot is "
                           "reserved (cart still loading, or empty)")
    if unreserved:
        await _open_time_chooser(page, fulfillment)
        dlg = page.locator("[role='dialog']").first
        scope = dlg if await dlg.count() else page
        free, paid = _slots_by_price(await _dialog_text(page))
        if slot_text:
            chosen = next((s for s in (free + paid)
                           if slot_text.replace(" ", "") in s.replace(" ", "")), None)
            if not chosen:
                raise RuntimeError(f"requested slot '{slot_text}' isn't offered; "
                                   f"available: {(free + paid)[:6]}")
        elif free:
            chosen = free[0]  # earliest FREE pickup slot — never auto-pick a paid express fee
        else:
            raise RuntimeError("no free pickup slots available right now — "
                               "tell me a specific time or try a later day")
        # Scope the click to the reserve dialog so a bare time string can't match unrelated page
        # text; '.first' still applies within the dialog.
        await scope.locator(f"text={chosen}").first.click()
        await human_pause()
        sel = page.locator(SELECTORS["select_time"]).first
        if await sel.count():
            await sel.click()
            await human_pause(2.0, 4.0)
        # SAFETY NET: the same clock time can exist as a paid express slot (earlier in DOM) and a
        # free one, so a bare-text '.first' click could grab the paid one even when we chose free.
        # When WE auto-picked the free default, verify no pickup/delivery fee was added — abort
        # PRE-commit (approval restored) rather than reserve a paid express slot.
        if not slot_text:
            body = " ".join((await page.locator("body").inner_text()).split())
            fee = re.search(r"(?:Curbside|Delivery)\s+fee\s+\$([\d.]+)", body)
            if fee and float(fee.group(1)) > 0:
                raise RuntimeError(
                    f"the auto-selected pickup slot added a ${fee.group(1)} fee — refusing a paid "
                    "express slot; ask the user for a specific free time or a later day")
    await _shot(page, order_id, "01-slot-reserved")

    start = page.locator(SELECTORS["start_checkout"]).first
    if not await start.count():
        raise RuntimeError("no 'Start checkout' button after reserving a slot (layout changed)")
    await start.click()
    await page.wait_for_load_state("domcontentloaded")
    await human_pause(2.0, 4.0)

    # Walk through the precheckout upsell ("/precheckout") to the real "/checkout".
    for _ in range(3):
        if "/checkout" in page.url and "/precheckout" not in page.url:
            break
        cont = page.locator(SELECTORS["precheckout_continue"]).first
        if await cont.count():
            await cont.click()
            try:
                await page.wait_for_url("**/checkout", timeout=20000)
            except Exception:
                await page.wait_for_load_state("domcontentloaded")
            await human_pause(2.0, 4.0)
        else:
            await human_pause(1.5, 2.5)
    await page.wait_for_timeout(2000)
    await _shot(page, order_id, "02-checkout")


async def _select_payment(page: Page) -> bool:
    """The saved primary card is normally pre-selected on /checkout, so 'Place order' is
    already enabled — just confirm that. Only if it's disabled do we click the saved card,
    matched by its card ROW ('Card number' / 'ending in' / masked '****') rather than brand
    text: brand words like 'Discover' otherwise match unrelated nav ('Discover our brands
    menu') and hang the walk for the full action timeout."""
    po = page.locator(SELECTORS["place_order"]).first
    if await po.count() and not await po.is_disabled():
        return True
    card = page.locator(
        "button:has-text('Card number'), button:has-text('ending in'), button:has-text('****')"
    ).first
    if await card.count():
        for _ in range(2):  # first click occasionally doesn't register
            await card.click()
            await human_pause(1.5, 3.0)
            if await po.count() and not await po.is_disabled():
                return True
    return await po.count() > 0 and not await po.is_disabled()


async def preview(fulfillment: str, order_id: str, headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await _assert_logged_in(page)
        await page.wait_for_timeout(6000)
        await _reserve_and_advance(page, fulfillment, None, order_id)
        ready = await _select_payment(page)
        total = await _estimated_total(page)
        await _shot(page, order_id, "03-review")
        return {
            "fulfillment": fulfillment,
            "estimated_total": total,
            "place_order_ready": ready,
            "screenshots": str(audit.screenshots_dir(order_id)),
        }


async def place(fulfillment: str, slot_text: str | None, order_id: str,
                dry_run: bool = True, headless: bool = True,
                max_total: float | None = None) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await _assert_logged_in(page)
        await page.wait_for_timeout(6000)
        await _reserve_and_advance(page, fulfillment, slot_text, order_id)

        if not await _select_payment(page):
            return {"status": "aborted", "reason": "no saved payment card on the checkout page",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        await human_pause()
        total = await _estimated_total(page)
        po = page.locator(SELECTORS["place_order"]).first
        if not await po.count():
            raise RuntimeError("Place order button not found — checkout flow changed")
        if await po.is_disabled():
            return {"status": "aborted", "reason": "Place order stayed disabled after selecting payment",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        await _shot(page, order_id, "04-final-review")

        if dry_run:
            return {"status": "dry_run", "estimated_total": total,
                    "stopped_before": "Place order click",
                    "screenshots": str(audit.screenshots_dir(order_id))}

        if max_total is not None and total is None:
            return {"status": "aborted", "reason": "could not read order total — refusing to place blind",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        if max_total is not None and total is not None and total > max_total:
            return {"status": "aborted",
                    "reason": f"on-screen total ${total:.2f} exceeds approved ${max_total:.2f}",
                    "screenshots": str(audit.screenshots_dir(order_id))}

        # ---- POINT OF NO RETURN ----
        # Once this click is dispatched the order is committed server-side. Anything that
        # raises AFTER this MUST NOT be treated as a safe-to-retry failure (that would risk
        # a duplicate order). So we never let a bare exception escape post-click: we always
        # return a structured result, marking the outcome 'placed' (confirmed) or
        # 'placed_unconfirmed' (committed but we couldn't read confirmation — human must
        # verify in order history before any re-place).
        await po.click()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await human_pause(3.0, 5.0)
            try:
                await _shot(page, order_id, "05-confirmation")
            except Exception:
                pass
            body = " ".join((await page.locator("body").inner_text()).split())
            conf = re.search(r"Order\s*#\s*([A-Z]{2,}[0-9]{6,})", body) or \
                re.search(r"\b(HEB[0-9]{8,})\b", body)
            placed_ok = "order is placed" in body.lower() or "order placed" in body.lower()
            if conf or placed_ok:
                return {"status": "placed", "estimated_total": total,
                        "confirmation": conf.group(1) if conf else None,
                        "placed_confirmed": placed_ok,
                        "screenshots": str(audit.screenshots_dir(order_id))}
            # Click landed but no confirmation text — treat as committed-but-unconfirmed.
            return {"status": "placed_unconfirmed", "estimated_total": total,
                    "confirmation": None, "placed_confirmed": False,
                    "reason": "Place order was clicked but no confirmation was read — "
                              "VERIFY in HEB order history before re-placing.",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        except Exception as e:
            # Exception after the commit click — do NOT re-raise (would let the caller
            # restore the approval and risk a duplicate). Report unconfirmed.
            return {"status": "placed_unconfirmed", "estimated_total": total,
                    "confirmation": None, "placed_confirmed": False,
                    "reason": f"Place order clicked, then {type(e).__name__} before "
                              "confirmation — VERIFY in HEB order history before re-placing.",
                    "screenshots": str(audit.screenshots_dir(order_id))}
