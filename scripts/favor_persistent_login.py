"""EXPERIMENT (and, if it works, the real Favor session): a dedicated Playwright-OWNED
persistent profile you log into once, including the SMS verification. Because Playwright
owns this profile from the start, it can drive it natively (no flaky CDP), and Favor's
fraud engine may remember this device after the first verification so later orders skip
the SMS step.

Run in Terminal (a real browser window opens):
    .venv/bin/python scripts/favor_persistent_login.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402

PROFILE = config.agent_home() / "profiles" / "favor-pw"
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-infobars"]


async def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), headless=False, args=ARGS,
            viewport={"width": 1280, "height": 860},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.favordelivery.com/login/", wait_until="domcontentloaded")
        print()
        print("A browser window is open. In it:")
        print("  1. Log into your Favor account (phone number → SMS code).")
        print("  2. Set your delivery address so the H-E-B Now storefront loads.")
        print("  3. OPTIONAL but ideal: add one cheap item and go all the way to the")
        print("     checkout/verify step ONCE here so this device gets verified.")
        print("  4. Do NOT place an order. Just get through any SMS verification.")
        print()
        input("When you're logged in (and past any SMS step), press Enter… ")
        await ctx.close()  # persistent context auto-saves to the profile dir
    print(f"\nSaved Favor session to {PROFILE}")
    print('Tell the agent "done" — it will test whether a second checkout skips the SMS.')


if __name__ == "__main__":
    asyncio.run(main())
