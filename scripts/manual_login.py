"""One-time manual HEB login that defeats the bot-block: opens a real, visible
Chromium window with the agent's persistent profile; YOU log in like a normal
customer; the script saves the session for all future headless agent runs.

Run in Terminal:  .venv/bin/python scripts/manual_login.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402
from heb_checkout.browser import LAUNCH_ARGS, USER_AGENT  # noqa: E402

PROFILE_DIR = config.agent_home() / "profiles" / "heb"


async def main() -> None:
    auth_path = config.auth_state_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            args=LAUNCH_ARGS,
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 860},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.heb.com", wait_until="domcontentloaded")

        print()
        print("A browser window is open on heb.com. In that window:")
        print("  1. Click 'Log in' and sign in with your HEB account")
        print("     (complete any verification it asks for)")
        print("  2. Confirm you can see your account/name in the top bar")
        print("  3. Optionally browse a product or two — looks human, helps trust")
        print()
        input("When you are logged in, come back here and press Enter... ")

        await context.storage_state(path=str(auth_path))
        await context.close()

    print(f"\nSession saved to {auth_path}")
    print('Tell the agent "done" — it will verify with session_status.')


if __name__ == "__main__":
    asyncio.run(main())
