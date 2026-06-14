"""Favor (favordelivery.com) automation engine. Drives the H-E-B Now storefront with
Playwright using the parked-Favor-Chrome session, mirroring heb_checkout's approach.

ALL site selectors live in SELECTORS so a UI change is a one-place fix. The storefront is
a React SPA backed by api.askfavor.com; search can fall back to that JSON API. Favor is
ADDRESS-keyed (not store_id like heb.com), and capped at ~25 items / on-demand window.

Verified live (2026-06-13): search (real catalog/prices), and building the cart from an
items list + reaching the checkout drawer. NOT yet verified: the logged-in checkout/payment
review screen — the cart-item count, final total, and Place-order button selectors past the
'checkout' button. Those need one SUPERVISED first-order pass (it approaches real payment),
exactly as HEB's place button was confirmed on its first real order. Keep
FAVOR_CHECKOUT_DRY_RUN=true until then.
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

# Selectors verified live against the logged-in H-E-B Now storefront (2026-06-13).
# Checkout/place-order selectors remain best-effort (browse/search/cart confirmed;
# the logged-in checkout screen was not driven) — keep FAVOR_CHECKOUT_DRY_RUN=true
# until a dry-run confirms them.
SELECTORS = {
    "storefront_link": "a[href*='order-delivery']",
    "address_input": "input[placeholder*='address' i], input[aria-label*='address' i]",
    "address_option": "[role='option'], [class*='autocomplete'] li, [class*='suggestion']",
    "search_box": "input[placeholder*='Search H-E-B' i], input[placeholder*='Search' i]",
    # item-card-{uuid} is the product; exclude item-card-title-/price-/image- descendants.
    "product_card": "[data-testid^='item-card-']:not([data-testid^='item-card-title-'])"
                    ":not([data-testid^='item-card-price-']):not([data-testid^='item-card-image'])",
    "product_name": "[data-testid^='item-card-title-']",
    "product_price": "[data-testid^='item-card-price-']",
    "add_button": "[data-testid='stepper-increment']",
    "qty_value": "[data-testid='stepper-value']",
    "cart_button": "button:has-text('Bag')",
    "item_limit_warning": "[data-testid*='item_limit_warning']",
    "delivery_fee": "[data-testid='delivery-fee-price']",
    # Cart-specific (must NOT match storefront product cards, which would over-count).
    # Best-effort/unverified for the checkout drawer — if it matches nothing the cap check
    # simply doesn't fire (safe), rather than falsely aborting.
    "cart_item": "[data-testid*='cart-item'], [data-testid*='bag-item'], [data-testid*='order-item'], [class*='CartItem']",
    "checkout_button": "[data-testid='checkout'], button:has-text('Checkout'), button:has-text('Go to checkout')",
    "eta_button": "[data-testid='eta-scheduling-button']",
    "checkout_estimate": "[data-testid='checkout-estimate-header']",
    "fulfillment_now": "button:has-text('Now'), :text('H-E-B Now')",
    "fulfillment_express": "button:has-text('Express'), :text('2 hour')",
    "place_order": "button:has-text('Place order'), button:has-text('Place Order')",
    "order_total": "[data-testid*='total'], [class*='total']",
    "confirmation": ":text('order is on its way'), :text('Order placed'), [class*='confirmation']",
}


async def human_pause(low: float = 0.8, high: float = 2.4) -> None:
    await asyncio.sleep(random.uniform(low, high))


def _pw_profile():
    """The dedicated Playwright-owned Favor profile (scripts/favor_persistent_login.py).
    Logged in + verified once; Playwright drives it natively (no flaky CDP). This is the
    reliable session for search + cart-build. (It still can't PLACE — Favor re-demands SMS
    on every automated checkout — so ordering stays semi-automated: agent builds, user
    places in the app.)"""
    d = config.agent_home() / "profiles" / "favor-pw"
    return d if d.exists() and any(d.iterdir()) else None


@asynccontextmanager
async def favor_page(headless: bool = True):
    """A page on the Favor storefront, using the persistent logged-in profile when present
    (reliable, no CDP), else a headless saved-cookie session. Favor's cart is session-bound
    AND its checkout re-verifies by SMS, so the order flow builds the cart in one session
    and hands off to the user to place."""
    prof = _pw_profile()
    async with async_playwright() as p:
        if prof:
            ctx = await p.chromium.launch_persistent_context(
                str(prof), headless=headless, args=LAUNCH_ARGS, user_agent=USER_AGENT)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                yield page
            finally:
                await ctx.close()  # persists to the profile dir
            return
        auth = config.favor_auth_state_path()
        if not auth.exists():
            raise RuntimeError(
                "No Favor session: run scripts/favor_persistent_login.py and log in "
                f"({prof or auth} missing)"
            )
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
    """On the H-E-B Now product storefront the product search box is present (the homepage
    only has the address search). Use the search box as the readiness signal."""
    return ("order-delivery" in page.url
            and await page.locator(SELECTORS["search_box"]).count() > 0)


async def _goto_storefront(page: Page, address: str) -> bool:
    """Reach the H-E-B Now product storefront. The parked session remembers the address,
    so favordelivery.com exposes an order-delivery link we can follow; otherwise fall back
    to typing the address. Returns True if the product storefront is ready."""
    if await _storefront_ready(page):
        return True
    # Follow the remembered storefront link (e.g. /order-delivery/h-e-b-now-5/2701).
    # Wait for it to render — the homepage is a React SPA too.
    try:
        await page.wait_for_selector(SELECTORS["storefront_link"], timeout=15000)
    except Exception:
        pass
    link = page.locator(SELECTORS["storefront_link"]).first
    if await link.count():
        href = await link.get_attribute("href")
        if href:
            await page.goto(STOREFRONT + href if href.startswith("/") else href,
                            wait_until="domcontentloaded", timeout=45000)
            # The storefront is a React SPA — wait for the product search box to render
            # rather than a fixed delay (it can take several seconds).
            try:
                await page.wait_for_selector(SELECTORS["search_box"], timeout=20000)
            except Exception:
                pass
            if await _storefront_ready(page):
                return True
    # Fall back to setting the address via the homepage autocomplete.
    box = page.locator(SELECTORS["address_input"]).first
    if await box.count() and address:
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
              "you've logged in via scripts/favor_persistent_login.py.",
}


async def _texts(page: Page, key: str, limit: int = 20) -> list[str]:
    out = []
    for el in (await page.locator(SELECTORS[key]).all())[:limit]:
        t = " ".join((await el.inner_text()).split())
        if t:
            out.append(t)
    return out


async def _run_search(page: Page, term: str) -> list:
    """Type a term into the storefront search and return the result product-card locators."""
    box = page.locator(SELECTORS["search_box"]).first
    if not await box.count():
        raise RuntimeError("Favor search box not found — storefront changed or address not set")
    await box.click()
    await box.fill(term)
    await page.keyboard.press("Enter")
    try:
        await page.wait_for_selector(SELECTORS["product_card"], timeout=15000)
    except Exception:
        pass
    await human_pause(1.0, 2.0)
    return await page.locator(SELECTORS["product_card"]).all()


async def search(term: str, address: str, limit: int = 8, headless: bool = True) -> dict:
    """Search the Favor H-E-B Now catalog for `term` at `address`."""
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _goto_storefront(page, address):
            return {"term": term, "count": 0, "results": [], **_ADDRESS_ABORT}
        cards = (await _run_search(page, term))[:limit]
        results = []
        for c in cards:
            name = " ".join((await c.locator(SELECTORS["product_name"]).first.inner_text()).split()) \
                if await c.locator(SELECTORS["product_name"]).count() else None
            price = parse_dollars(await c.locator(SELECTORS["product_price"]).first.inner_text()) \
                if await c.locator(SELECTORS["product_price"]).count() else None
            if name:
                results.append({"name": name, "price": price})
        return {"term": term, "count": len(results), "results": results}


def _norm_items(items: list) -> list[dict]:
    """Accept ['milk', ...] or [{'name'/'term':..., 'quantity':...}, ...]."""
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"term": it, "quantity": 1})
        else:
            out.append({"term": it.get("term") or it.get("name") or "",
                        "quantity": int(it.get("quantity", 1) or 1)})
    return [i for i in out if i["term"]]


async def _add_items(page: Page, items: list[dict]) -> list[dict]:
    """Search + add each item to the cart IN THIS SESSION. Favor's cart is session-bound,
    so building it and checking out must happen in one favor_page() call."""
    added = []
    for it in items:
        cards = await _run_search(page, it["term"])
        if not cards:
            added.append({"term": it["term"], "status": "not_found"})
            continue
        card = cards[0]
        name = " ".join((await card.locator(SELECTORS["product_name"]).first.inner_text()).split()) \
            if await card.locator(SELECTORS["product_name"]).count() else it["term"]
        add = card.locator(SELECTORS["add_button"]).first
        if not await add.count():
            added.append({"term": it["term"], "status": "no_add_button"})
            continue
        for _ in range(max(1, it["quantity"])):
            await add.click()
            await human_pause(0.6, 1.4)
        added.append({"term": it["term"], "item": name, "quantity": it["quantity"], "status": "added"})
    return added


async def _open_checkout(page):
    cart = page.locator(SELECTORS["checkout_button"]).first
    if not await cart.count():
        cart = page.locator(SELECTORS["cart_button"]).first  # open the bag drawer first
        if await cart.count():
            await cart.click()
            await human_pause()
            cart = page.locator(SELECTORS["checkout_button"]).first
    if not await cart.count():
        return False
    await cart.click()
    await page.wait_for_load_state("domcontentloaded")
    await human_pause(1.5, 3.0)
    return True


async def preview(items: list, address: str, order_id: str, fulfillment: str = "now",
                  headless: bool = True) -> dict:
    """Build the cart from `items` and walk to checkout review: fee/total/ETA. One session.
    Never places an order."""
    norm = _norm_items(items)
    if not norm:
        return {"status": "empty_cart", "reason": "no items given to favor preview"}
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _goto_storefront(page, address):
            return _ADDRESS_ABORT
        added = await _add_items(page, norm)
        if not await _open_checkout(page):
            return {"status": "empty_cart", "added": added,
                    "reason": "could not reach checkout — nothing added or cart UI changed"}
        await page.screenshot(path=str(audit.screenshots_dir(order_id) / "favor-01-review.png"))
        item_count = await page.locator(SELECTORS["cart_item"]).count()
        total = parse_dollars((await _texts(page, "order_total", 1) or [None])[0])
        out = {"fulfillment": fulfillment, "added": added, "estimated_total": total,
               "item_count": item_count or None,
               "place_order_ready": await page.locator(SELECTORS["place_order"]).first.count() > 0,
               "screenshots": str(audit.screenshots_dir(order_id))}
        if item_count and item_count > ITEM_CAP:
            out["warning"] = f"{item_count} items exceeds Favor's {ITEM_CAP}-item cap"
        return out


async def place(items: list, address: str, order_id: str, fulfillment: str = "now",
                dry_run: bool = True, max_total: float | None = None,
                headless: bool = True) -> dict:
    """Build the cart from `items` and complete Favor checkout IN ONE SESSION. dry_run
    stops one click before purchase. Mirrors HEB money-safety: aborts on unreadable/
    over-limit total, never raises after the commit click (returns placed_unconfirmed)."""
    norm = _norm_items(items)
    if not norm:
        return {"status": "empty_cart", "reason": "no items given to favor place"}
    async with favor_page(headless=headless) as page:
        await page.goto(STOREFRONT, wait_until="domcontentloaded")
        await human_pause()
        if not await _goto_storefront(page, address):
            return _ADDRESS_ABORT
        added = await _add_items(page, norm)
        if not await _open_checkout(page):
            return {"status": "empty_cart", "added": added,
                    "reason": "could not reach checkout — nothing added or cart UI changed"}
        # Enforce Favor's hard on-demand item cap BEFORE the point of no return, so we
        # never hit a server-side rejection in a placed_unconfirmed state.
        item_count = await page.locator(SELECTORS["cart_item"]).count()
        if item_count and item_count > ITEM_CAP:
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
