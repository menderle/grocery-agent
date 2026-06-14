"""Favor (favordelivery.com) automation engine. Drives the H-E-B Now storefront with
Playwright using the parked-Favor-Chrome session, mirroring heb_checkout's approach.

ALL site selectors live in SELECTORS so a UI change is a one-place fix. The storefront is
a React SPA backed by api.askfavor.com; search can fall back to that JSON API. Favor is
ADDRESS-keyed (not store_id like heb.com), and capped at ~25 items / on-demand window.

Best-effort selectors: browse/search/cart verified live; the logged-in checkout step is
unverified — keep FAVOR_CHECKOUT_DRY_RUN=true until confirmed against a real Favor login.
"""

import asyncio
import random
import re
from contextlib import asynccontextmanager

from playwright.async_api import Page, async_playwright

from heb_checkout import audit, config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
               "--no-default-browser-check", "--disable-infobars"]

STOREFRONT = "https://www.favordelivery.com"
ITEM_CAP = 25  # Favor on-demand hard cap

SELECTORS = {
    "address_input": "input[placeholder*='address' i], input[aria-label*='address' i]",
    "address_option": "[role='option'], [class*='autocomplete'] li, [class*='suggestion']",
    "search_box": "input[placeholder*='Search' i]",
    "product_card": "[data-testid*='product'], [class*='ProductCard'], [class*='product-card']",
    "product_name": "[class*='name'], [data-testid*='name']",
    "product_price": "[class*='price'], [data-testid*='price']",
    "add_button": "button[aria-label*='add' i], button:has-text('+')",
    "cart_pill": "[class*='cart'], [data-testid*='cart']",
    "cart_item": "[data-testid*='cart-item'], [class*='CartItem']",
    "checkout_button": "button:has-text('Checkout'), button:has-text('Go to checkout')",
    "fulfillment_now": "button:has-text('Now'), :text('H-E-B Now')",
    "fulfillment_express": "button:has-text('Express'), :text('2 hour')",
    "place_order": "button:has-text('Place order'), button:has-text('Place Order')",
    "order_total": "[class*='total'], [data-testid*='total']",
    "confirmation": "[class*='confirmation'], :text('order is on its way'), :text('Order placed')",
}


async def human_pause(low: float = 0.8, high: float = 2.4) -> None:
    await asyncio.sleep(random.uniform(low, high))


@asynccontextmanager
async def favor_page(headless: bool = True):
    """Playwright page using the saved Favor session (favor-auth.json), refreshed from the
    parked Favor Chrome by scripts/sync_parked_favor_session.py."""
    auth = config.favor_auth_state_path()
    if not auth.exists():
        raise RuntimeError(
            f"No Favor session at {auth}. Run scripts/start_parked_favor_chrome.sh, log in "
            "to your Favor account, then scripts/sync_parked_favor_session.py."
        )
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=LAUNCH_ARGS)
        ctx = await browser.new_context(user_agent=USER_AGENT, storage_state=str(auth))
        page = await ctx.new_page()
        try:
            yield page
            await ctx.storage_state(path=str(auth))
        finally:
            await browser.close()


def parse_dollars(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\$\s*([\d,]+\.?\d*)", text)
    return float(m.group(1).replace(",", "")) if m else None


async def _storefront_ready(page: Page) -> bool:
    return "order-delivery" in page.url or await page.locator(SELECTORS["search_box"]).count() > 0


async def _ensure_address(page: Page, address: str) -> bool:
    """Set the delivery address (Favor is address-keyed). Returns True if the storefront
    is in a usable state afterward, so callers can abort cleanly instead of hitting
    cryptic selector-not-found errors downstream."""
    if await _storefront_ready(page):
        return True
    box = page.locator(SELECTORS["address_input"]).first
    if await box.count():
        await box.fill(address)
        await human_pause()
        opt = page.locator(SELECTORS["address_option"]).first
        if await opt.count():
            await opt.click()
            await human_pause(1.5, 3.0)
    return await _storefront_ready(page)


_ADDRESS_ABORT = {
    "status": "aborted",
    "reason": "Favor storefront/address not ready — check FAVOR_DEFAULT_ADDRESS and that "
              "the parked Favor session is logged in (scripts/sync_parked_favor_session.py).",
}


async def _texts(page: Page, key: str, limit: int = 20) -> list[str]:
    out = []
    for el in (await page.locator(SELECTORS[key]).all())[:limit]:
        t = " ".join((await el.inner_text()).split())
        if t:
            out.append(t)
    return out


async def search(term: str, address: str, limit: int = 8, headless: bool = True) -> dict:
    """Search the Favor H-E-B Now catalog for `term` at `address`."""
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _ensure_address(page, address):
            return {"term": term, "count": 0, "results": [], **_ADDRESS_ABORT}
        box = page.locator(SELECTORS["search_box"]).first
        if not await box.count():
            raise RuntimeError("Favor search box not found — storefront changed or address not set")
        await box.fill(term)
        await page.keyboard.press("Enter")
        await human_pause(2.0, 3.5)
        cards = (await page.locator(SELECTORS["product_card"]).all())[:limit]
        results = []
        for c in cards:
            name = " ".join((await c.locator(SELECTORS["product_name"]).first.inner_text()).split()) \
                if await c.locator(SELECTORS["product_name"]).count() else None
            price = parse_dollars(await c.locator(SELECTORS["product_price"]).first.inner_text()) \
                if await c.locator(SELECTORS["product_price"]).count() else None
            if name:
                results.append({"name": name, "price": price})
        return {"term": term, "count": len(results), "results": results}


async def preview(address: str, order_id: str, fulfillment: str = "now",
                  headless: bool = True) -> dict:
    """Walk to the Favor checkout review and report fee/total/ETA. Never places an order."""
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _ensure_address(page, address):
            return _ADDRESS_ABORT
        cart = page.locator(SELECTORS["checkout_button"]).first
        if not await cart.count():
            return {"status": "empty_cart",
                    "reason": "no checkout button — Favor cart is empty (add items first)"}
        await cart.click()
        await page.wait_for_load_state("domcontentloaded")
        await human_pause()
        await page.screenshot(path=str(audit.screenshots_dir(order_id) / "favor-01-review.png"))
        item_count = await page.locator(SELECTORS["cart_item"]).count()
        total = parse_dollars((await _texts(page, "order_total", 1) or [None])[0])
        out = {"fulfillment": fulfillment, "estimated_total": total, "item_count": item_count or None,
               "place_order_ready": await page.locator(SELECTORS["place_order"]).first.count() > 0,
               "screenshots": str(audit.screenshots_dir(order_id))}
        if item_count > ITEM_CAP:
            out["warning"] = f"{item_count} items exceeds Favor's {ITEM_CAP}-item cap — remove some before ordering"
        return out


async def place(address: str, order_id: str, fulfillment: str = "now",
                dry_run: bool = True, max_total: float | None = None,
                headless: bool = True) -> dict:
    """Complete Favor checkout. dry_run stops one click before purchase. Mirrors HEB's
    money-safety: aborts on unreadable/over-limit total, never raises after the commit
    click (returns placed_unconfirmed)."""
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _ensure_address(page, address):
            return _ADDRESS_ABORT
        cart = page.locator(SELECTORS["checkout_button"]).first
        if not await cart.count():
            return {"status": "empty_cart", "reason": "Favor cart is empty"}
        await cart.click()
        await page.wait_for_load_state("domcontentloaded")
        await human_pause()
        # Enforce Favor's hard on-demand item cap BEFORE the point of no return, so we
        # never hit a server-side rejection in a placed_unconfirmed state.
        item_count = await page.locator(SELECTORS["cart_item"]).count()
        if item_count > ITEM_CAP:
            return {"status": "aborted",
                    "reason": f"Favor cart has {item_count} items, over the {ITEM_CAP}-item "
                              "on-demand cap — remove some or use HEB scheduled delivery",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        total = parse_dollars((await _texts(page, "order_total", 1) or [None])[0])
        po = page.locator(SELECTORS["place_order"]).first
        if not await po.count():
            raise RuntimeError("Favor place-order button not found — checkout flow changed (verify selectors)")
        await page.screenshot(path=str(audit.screenshots_dir(order_id) / "favor-02-final.png"))

        if dry_run:
            return {"status": "dry_run", "estimated_total": total,
                    "stopped_before": "Place order click",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        if max_total is not None and total is None:
            return {"status": "aborted", "reason": "could not read Favor total — refusing to place blind",
                    "screenshots": str(audit.screenshots_dir(order_id))}
        if max_total is not None and total is not None and total > max_total:
            return {"status": "aborted",
                    "reason": f"Favor total ${total:.2f} exceeds approved ${max_total:.2f}",
                    "screenshots": str(audit.screenshots_dir(order_id))}

        # ---- POINT OF NO RETURN (same money-safety contract as HEB) ----
        await po.click()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await human_pause(3.0, 5.0)
            try:
                await page.screenshot(path=str(audit.screenshots_dir(order_id) / "favor-03-confirm.png"))
            except Exception:
                pass
            body = " ".join((await page.locator("body").inner_text()).split())
            ok = any(s in body.lower() for s in ("order placed", "on its way", "order is on its way"))
            return {"status": "placed" if ok else "placed_unconfirmed", "estimated_total": total,
                    "placed_confirmed": ok,
                    **({} if ok else {"reason": "Favor place clicked but no confirmation read — "
                                                "verify in the Favor app before re-placing."}),
                    "screenshots": str(audit.screenshots_dir(order_id))}
        except Exception as e:
            return {"status": "placed_unconfirmed", "estimated_total": total, "placed_confirmed": False,
                    "reason": f"Favor place clicked, then {type(e).__name__} before confirmation — "
                              "verify in the Favor app before re-placing.",
                    "screenshots": str(audit.screenshots_dir(order_id))}
