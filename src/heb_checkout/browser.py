"""Shared Playwright context matching texas-grocery-mcp's fingerprint exactly
(same UA, launch args, and auth.json storage state) so HEB sees one consistent client."""

import asyncio
import random
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

from . import config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
]


@asynccontextmanager
async def heb_page(headless: bool = True):
    auth_path = config.auth_state_path()
    if not auth_path.exists():
        raise RuntimeError(
            f"No HEB session at {auth_path}. Log in first via texas-grocery-mcp "
            "(its authenticate/refresh_session tool) so checkout can reuse the session."
        )
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=LAUNCH_ARGS)
        context = await browser.new_context(user_agent=USER_AGENT, storage_state=str(auth_path))
        page = await context.new_page()
        try:
            yield page
            # Persist any refreshed cookies back for texas-grocery-mcp too.
            await context.storage_state(path=str(auth_path))
        finally:
            await browser.close()


async def human_pause(low: float = 0.8, high: float = 2.4) -> None:
    """Human-paced delay between actions; keeps automation under bot-detection thresholds."""
    await asyncio.sleep(random.uniform(low, high))
