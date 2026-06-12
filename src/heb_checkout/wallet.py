"""Drives the heb.com wallet (saved payment methods): list, add, set default, remove.
Selectors are isolated in WALLET_SELECTORS and, like checkout, are best-effort until
verified against the live logged-in site. Payment forms are often inside a payment-
processor iframe, so every field lookup searches all frames.

Privacy rules enforced here:
  - no screenshots while a card form is open (a screenshot would capture the full PAN)
  - returned/logged data only ever contains last4
"""

from playwright.async_api import Page

from .browser import heb_page, human_pause
from .cards import last4

WALLET_URL = "https://www.heb.com/account/payment-methods"

WALLET_SELECTORS = {
    "card_row": "[data-testid*='payment-method'], [class*='payment-method'], [class*='saved-card']",
    "add_button": "button:has-text('Add'), a:has-text('Add payment')",
    "number_field": "input[name*='card' i][name*='number' i], input[autocomplete='cc-number'], #cardNumber",
    "expiry_field": "input[autocomplete='cc-exp'], input[name*='expir' i], #expiryDate",
    "cvv_field": "input[autocomplete='cc-csc'], input[name*='cvv' i], input[name*='securityCode' i]",
    "name_field": "input[autocomplete='cc-name'], input[name*='cardholder' i], input[name*='nameOnCard' i]",
    "zip_field": "input[autocomplete='postal-code'], input[name*='zip' i], input[name*='postal' i]",
    "default_checkbox": "input[type='checkbox'][name*='default' i], label:has-text('default') input",
    "save_button": "button:has-text('Save'), button:has-text('Add card')",
    "set_default_button": "button:has-text('Set as default'), button:has-text('Make default')",
    "remove_button": "button:has-text('Remove'), button:has-text('Delete')",
    "confirm_remove": "button:has-text('Confirm'), button:has-text('Yes')",
}


async def _fill_in_frames(page: Page, selector_key: str, value: str) -> bool:
    """Fill the first matching field on the page or any iframe (processor-hosted forms)."""
    selector = WALLET_SELECTORS[selector_key]
    for frame in page.frames:
        field = frame.locator(selector).first
        if await field.count():
            await field.fill(value)
            await human_pause(0.3, 0.9)
            return True
    return False


async def _rows_text(page: Page) -> list[str]:
    rows = []
    for el in (await page.locator(WALLET_SELECTORS["card_row"]).all())[:10]:
        text = " ".join((await el.inner_text()).split())
        if text:
            rows.append(text)
    return rows


async def list_methods(headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(WALLET_URL, wait_until="domcontentloaded")
        await human_pause()
        return {"payment_methods": await _rows_text(page)}


async def add_card(card: dict, set_default: bool = True, remove_old: bool = True,
                   headless: bool = True) -> dict:
    """Add `card` to the HEB wallet, optionally set default and remove other cards.
    Returns last4-only data; the full number exists only in the form fill."""
    new_last4 = last4(card["number"])
    async with heb_page(headless=headless) as page:
        await page.goto(WALLET_URL, wait_until="domcontentloaded")
        await human_pause()
        before = await _rows_text(page)

        await page.locator(WALLET_SELECTORS["add_button"]).first.click()
        await human_pause()

        if not await _fill_in_frames(page, "number_field", card["number"]):
            raise RuntimeError("card number field not found — wallet UI may have changed (fix WALLET_SELECTORS)")
        await _fill_in_frames(page, "expiry_field", card["expiry"])
        await _fill_in_frames(page, "cvv_field", card["cvv"])
        await _fill_in_frames(page, "name_field", card.get("name", ""))
        await _fill_in_frames(page, "zip_field", card.get("zip", ""))

        default_box = page.locator(WALLET_SELECTORS["default_checkbox"]).first
        if set_default and await default_box.count() and not await default_box.is_checked():
            await default_box.check()
        await human_pause()
        await page.locator(WALLET_SELECTORS["save_button"]).first.click()
        await page.wait_for_load_state("domcontentloaded")
        await human_pause(2.0, 4.0)

        after = await _rows_text(page)
        if not any(new_last4 in row for row in after):
            raise RuntimeError(
                f"card ending {new_last4} not visible after save — likely declined "
                "(is the prepaid card REGISTERED with name+ZIP at the issuer? AVS rejects unregistered gift cards)"
            )

        # Set default via button if the form had no checkbox.
        if set_default and await default_box.count() == 0:
            row = page.locator(WALLET_SELECTORS["card_row"], has_text=new_last4).first
            btn = row.locator(WALLET_SELECTORS["set_default_button"]).first
            if await btn.count():
                await btn.click()
                await human_pause()

        removed = []
        if remove_old:
            for row_text in after:
                if new_last4 in row_text:
                    continue
                old = last4(row_text.replace(" ", ""))  # best-effort digits from row text
                row = page.locator(WALLET_SELECTORS["card_row"], has_text=old).first
                btn = row.locator(WALLET_SELECTORS["remove_button"]).first
                if await btn.count():
                    await btn.click()
                    await human_pause()
                    confirm = page.locator(WALLET_SELECTORS["confirm_remove"]).first
                    if await confirm.count():
                        await confirm.click()
                        await human_pause()
                    removed.append(old)

        return {
            "status": "saved",
            "new_card_last4": new_last4,
            "set_default": set_default,
            "removed_old_last4": removed,
            "wallet_before": len(before),
            "wallet_after": len(await _rows_text(page)),
        }


async def remove_card(card_last4: str, headless: bool = True) -> dict:
    async with heb_page(headless=headless) as page:
        await page.goto(WALLET_URL, wait_until="domcontentloaded")
        await human_pause()
        row = page.locator(WALLET_SELECTORS["card_row"], has_text=card_last4).first
        if not await row.count():
            raise RuntimeError(f"no saved card ending {card_last4}")
        await row.locator(WALLET_SELECTORS["remove_button"]).first.click()
        await human_pause()
        confirm = page.locator(WALLET_SELECTORS["confirm_remove"]).first
        if await confirm.count():
            await confirm.click()
        await human_pause()
        return {"status": "removed", "last4": card_last4}
