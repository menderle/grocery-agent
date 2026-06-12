"""Drives heb.com checkout with Playwright. ALL site selectors live in SELECTORS so a
UI change is a one-place fix. Nothing here is charged until place(dry_run=False) clicks
the final button; every step screenshots into the order's audit folder.

NOTE: selectors are best-effort until first verified against a logged-in session
(Phase 2 verification in the plan). Run exclusively with dry_run=True until then.
"""

import re

from playwright.async_api import Page

from . import audit
from .browser import heb_page, human_pause


def parse_dollars(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\$\s*([\d,]+\.?\d*)", text)
    return float(m.group(1).replace(",", "")) if m else None

CART_URL = "https://www.heb.com/cart"
CHECKOUT_URL = "https://www.heb.com/checkout"

SELECTORS = {
    "cart_total": "[data-qe-id='cartTotal'], [data-testid*='total']",
    "checkout_button": "button:has-text('Checkout'), a[href*='/checkout']",
    "fulfillment_pickup": "button:has-text('Pickup'), [data-testid*='pickup']",
    "fulfillment_delivery": "button:has-text('Delivery'), [data-testid*='delivery']",
    "slot_option": "[data-testid*='timeslot'], [class*='time-slot'], button[aria-label*='Reserve']",
    "saved_payment": "[data-testid*='payment'] :text('ending in'), [class*='saved-card']",
    "order_total": "[data-testid*='orderTotal'], [class*='order-total']",
    "place_order_button": "button:has-text('Place order'), button:has-text('Place Order')",
    "confirmation_number": "[data-testid*='confirmation'], :text('Order #')",
}


async def _shot(page: Page, order_id: str, name: str) -> str:
    path = audit.screenshots_dir(order_id) / f"{name}.png"
    await page.screenshot(path=str(path), full_page=False)
    return str(path)


async def _click_checkout(page: Page) -> None:
    button = page.locator(SELECTORS["checkout_button"]).first
    if not await button.count():
        raise RuntimeError(
            "no checkout button on the cart page — the HEB cart is probably empty "
            "(add items first), or the cart page layout changed (fix SELECTORS)"
        )
    await button.click()
    await page.wait_for_load_state("domcontentloaded")
    await human_pause()


async def _texts(page: Page, key: str, limit: int = 30) -> list[str]:
    found = []
    for el in (await page.locator(SELECTORS[key]).all())[:limit]:
        text = (await el.inner_text()).strip()
        if text:
            found.append(" ".join(text.split()))
    return found


async def get_slots(fulfillment: str, headless: bool = True) -> dict:
    """List available time slots for 'pickup' or 'delivery' without touching the order."""
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await human_pause()
        await _click_checkout(page)
        key = "fulfillment_pickup" if fulfillment == "pickup" else "fulfillment_delivery"
        toggle = page.locator(SELECTORS[key]).first
        if await toggle.count():
            await toggle.click()
            await human_pause()
        slots = await _texts(page, "slot_option")
        return {"fulfillment": fulfillment, "slots": slots, "slot_count": len(slots)}


async def preview(fulfillment: str, order_id: str, headless: bool = True) -> dict:
    """Walk to the final review screen and report totals/payment. Never places an order."""
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await human_pause()
        cart_total = await _texts(page, "cart_total", limit=1)
        await _shot(page, order_id, "01-cart")
        await _click_checkout(page)
        await _shot(page, order_id, "02-checkout")
        payment = await _texts(page, "saved_payment", limit=3)
        order_total = await _texts(page, "order_total", limit=1)
        return {
            "fulfillment": fulfillment,
            "cart_total": cart_total[0] if cart_total else None,
            "order_total": order_total[0] if order_total else None,
            "saved_payment": payment,
            "screenshots": str(audit.screenshots_dir(order_id)),
        }


async def place(
    fulfillment: str,
    slot_text: str | None,
    order_id: str,
    dry_run: bool = True,
    headless: bool = True,
    max_total: float | None = None,
) -> dict:
    """Complete checkout. dry_run=True stops one click before purchase and screenshots
    the final screen — this is the only function that can spend money."""
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await human_pause()
        await _click_checkout(page)

        key = "fulfillment_pickup" if fulfillment == "pickup" else "fulfillment_delivery"
        toggle = page.locator(SELECTORS[key]).first
        if await toggle.count():
            await toggle.click()
            await human_pause()

        if slot_text:
            slot = page.locator(SELECTORS["slot_option"], has_text=slot_text).first
            if not await slot.count():
                raise RuntimeError(f"slot matching {slot_text!r} not found")
            await slot.click()
            await human_pause()
        await _shot(page, order_id, "03-slot-selected")

        order_total = await _texts(page, "order_total", limit=1)
        final_button = page.locator(SELECTORS["place_order_button"]).first
        if not await final_button.count():
            raise RuntimeError("place-order button not found — checkout flow may have changed")
        await _shot(page, order_id, "04-final-review")

        if dry_run:
            return {
                "status": "dry_run",
                "order_total": order_total[0] if order_total else None,
                "stopped_before": "place_order click",
                "screenshots": str(audit.screenshots_dir(order_id)),
            }

        # Last-line guard: the on-screen total must be readable AND within what policy
        # evaluated. An unreadable total means we cannot verify the charge — never
        # place a live order blind.
        scraped = parse_dollars(order_total[0] if order_total else None)
        if max_total is not None and scraped is None:
            return {
                "status": "aborted",
                "reason": "could not read the on-screen order total — refusing to place a live "
                          "order unverified (order_total selector may need fixing)",
                "screenshots": str(audit.screenshots_dir(order_id)),
            }
        if max_total is not None and scraped is not None and scraped > max_total:
            return {
                "status": "aborted",
                "reason": f"on-screen total ${scraped:.2f} exceeds approved ${max_total:.2f}",
                "screenshots": str(audit.screenshots_dir(order_id)),
            }

        await final_button.click()
        await page.wait_for_load_state("domcontentloaded")
        await human_pause()
        confirmation = await _texts(page, "confirmation_number", limit=2)
        await _shot(page, order_id, "05-confirmation")
        return {
            "status": "placed",
            "order_total": order_total[0] if order_total else None,
            "confirmation": confirmation,
            "screenshots": str(audit.screenshots_dir(order_id)),
        }
