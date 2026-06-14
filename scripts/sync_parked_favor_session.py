"""Sync the Favor session FROM the parked Favor Chrome (start_parked_favor_chrome.sh,
port 9223) into favor-auth.json. Mirrors sync_parked_session.py for HEB. Atomic write.

Exit 0 = synced a logged-in Favor session, 1 = parked Favor Chrome missing or logged out."""

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402

PORT = config.favor_cdp_port()


def _parked_up() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


async def main() -> int:
    if not _parked_up():
        print(f"parked Favor Chrome not running (port {PORT}) — run scripts/start_parked_favor_chrome.sh")
        return 1
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        try:
            await page.goto("https://www.favordelivery.com", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            print(f"nudge failed ({type(e).__name__}) — reading cookies anyway")
        state = await ctx.storage_state()
        await page.close()
        await browser.close()

    favor_cookies = [c for c in state.get("cookies", [])
                     if "favordelivery.com" in c.get("domain", "") or "askfavor.com" in c.get("domain", "")]
    # A logged-in Favor session carries auth/session cookies; if there are essentially
    # none, the parked window isn't logged in.
    if len(favor_cookies) < 2:
        print("parked Favor Chrome appears logged OUT — log into Favor in that window")
        return 1
    _atomic_write(config.favor_auth_state_path(), json.dumps(state))
    print(f"synced {len(favor_cookies)} Favor cookies to {config.favor_auth_state_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
