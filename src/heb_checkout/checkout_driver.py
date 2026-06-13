"""Drives heb.com checkout with Playwright. Flow verified against the live site
(2026-06-12), curbside at Burnet Rd:

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
from .browser import heb_page, human_pause

CART_URL = "https://www.heb.com/cart"

SELECTORS = {
    "choose_time": "button:has-text('Choose pickup time'), button:has-text('Choose delivery time'), button:has-text('Change time')",
    "fulfillment_tab": "button[role='tab'], [role='tablist'] button",  # Curbside / Delivery
    "scheduled_expander": "text=Scheduled",
    "select_time": "button:has-text('Select this time')",
    "start_checkout": "button:has-text('Start checkout')",
    "precheckout_continue": "a:has-text('Continue'), button:has-text('Continue')",
    "place_order": "button:has-text('Place order')",
}


def parse_dollars(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\$\s*([\d,]+\.?\d*)", text)
    return float(m.group(1).replace(",", "")) if m else None


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


async def _slot_texts(page: Page) -> list[str]:
    """Visible time-slot labels like '7:00–7:30 AM' in the reserve dialog."""
    dlg = page.locator("[role='dialog']").first
    scope = dlg if await dlg.count() else page
    text = " ".join((await scope.inner_text()).split())
    return re.findall(r"\d{1,2}:\d{2}[–-]\d{1,2}:\d{2}\s*[AP]M", text)


async def get_slots(fulfillment: str, headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)  # cart fulfillment controls render async
        await _open_time_chooser(page, fulfillment)
        slots = await _slot_texts(page)
        return {"fulfillment": fulfillment, "slots": slots, "slot_count": len(slots)}


async def _reserve_and_advance(page: Page, fulfillment: str, slot_text: str | None,
                               order_id: str) -> None:
    """Cart → reserve a slot (unless one is already reserved) → Start checkout →
    past the precheckout upsell."""
    start = page.locator(SELECTORS["start_checkout"]).first
    if not await start.count():
        # No slot reserved yet — go through the time chooser.
        await _open_time_chooser(page, fulfillment)
        slots = await _slot_texts(page)
        if not slots:
            raise RuntimeError("no time slots offered (store/day may be full)")
        target = slot_text
        if target:
            target = next((s for s in slots if slot_text.replace(" ", "") in s.replace(" ", "")), None)
        chosen = target or slots[0]
        await page.locator(f"text={chosen}").first.click()
        await human_pause()
        sel = page.locator(SELECTORS["select_time"]).first
        if await sel.count():
            await sel.click()
            await human_pause(2.0, 4.0)
        start = page.locator(SELECTORS["start_checkout"]).first
    await _shot(page, order_id, "01-slot-reserved")

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
    """Click the saved card so 'Place order' enables. Returns True once Place order is
    actually enabled (verified), not merely clicked."""
    po = page.locator(SELECTORS["place_order"]).first
    for label in ("Mastercard", "Visa", "Discover", "American Express", "ending"):
        card = page.locator("button", has=page.locator(f"text={label}")).first
        if not await card.count():
            continue
        for _ in range(2):  # first click occasionally doesn't register
            await card.click()
            await human_pause(1.5, 3.0)
            if await po.count() and not await po.is_disabled():
                return True
    return await po.count() > 0 and not await po.is_disabled()


async def preview(fulfillment: str, order_id: str, headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(CART_URL, wait_until="domcontentloaded")
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

        await po.click()
        await page.wait_for_load_state("domcontentloaded")
        await human_pause(3.0, 5.0)
        await _shot(page, order_id, "05-confirmation")
        body = " ".join((await page.locator("body").inner_text()).split())
        conf = re.search(r"[Oo]rder\s*#?\s*(\w[\w-]{4,})", body)
        return {"status": "placed", "estimated_total": total,
                "confirmation": conf.group(0) if conf else None,
                "screenshots": str(audit.screenshots_dir(order_id))}
