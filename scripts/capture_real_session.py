"""Capture an HEB session from REAL Google Chrome (not Playwright's Chromium, which
HEB's bot detection fingerprints and blocks). Launches genuine Chrome with a debug
port and a dedicated profile, you log in like a normal customer, then we read the
session — including HEB's trust cookie — into auth.json for the agent.

Run in Terminal:  .venv/bin/python scripts/capture_real_session.py
"""

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
PROFILE_DIR = config.agent_home() / "profiles" / "heb-chrome"


def _debugger_ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2):
            return True
    except Exception:
        return False


async def main() -> None:
    auth_path = config.auth_state_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    if _debugger_ready():
        sys.exit(
            f"Something is already using port {PORT}. Quit any Chrome started with a "
            "debugging port and re-run."
        )

    print("Launching your real Google Chrome (dedicated profile)…")
    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "https://www.heb.com"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        if _debugger_ready():
            break
        time.sleep(0.5)
    else:
        proc.terminate()
        sys.exit("Chrome did not expose its debugging port. Re-run, or tell me and we'll try another way.")

    print()
    print("In the Chrome window that just opened:")
    print("  1. Click 'Log in' and sign in to your HEB account")
    print("     (check 'keep me signed in' if offered — extends the session)")
    print("  2. Confirm you see your name / account in the top bar")
    print("  3. Set your home store if it asks")
    print()
    input("When you're logged in, come back here and press Enter… ")

    hashes_file = Path(__file__).resolve().parents[1] / "config" / "graphql-hashes.json"
    captured: dict[str, str] = {}

    def sniff(req):
        if "/graphql" not in req.url or not req.post_data:
            return
        try:
            payload = json.loads(req.post_data)
        except Exception:
            return
        for e in (payload if isinstance(payload, list) else [payload]):
            op = e.get("operationName")
            sha = (e.get("extensions") or {}).get("persistedQuery", {}).get("sha256Hash")
            if op and sha:
                captured[op] = sha

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        context = browser.contexts[0]
        page = await context.new_page()
        page.on("request", sniff)

        # Drive a few real actions so HEB emits every persisted-query hash we rely on
        # (search/typeahead, store search, cart, slot/fulfillment) — harvested live.
        print("harvesting current API hashes (search, cart, slots)…")
        try:
            await page.goto("https://www.heb.com", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            await page.goto("https://www.heb.com/search?q=milk", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            await page.goto("https://www.heb.com/product-detail/p/325140", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            add = page.locator("button:has-text('Add to cart')").first
            if await add.count():
                await add.click()
                await page.wait_for_timeout(4000)
            await page.goto("https://www.heb.com/cart", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            ct = page.locator("button:has-text('Choose pickup time'), button:has-text('Change time')").first
            if await ct.count():
                await ct.click()
                await page.wait_for_timeout(4000)
        except Exception as e:
            print(f"  (hash harvest partial: {type(e).__name__})")

        state = await context.storage_state()
        heb_cookies = [c for c in state.get("cookies", []) if "heb.com" in c.get("domain", "")]
        trust = any(c["name"] in ("reese84", "sat", "visid_incap") for c in heb_cookies)
        auth_path.write_text(json.dumps(state))
        await page.close()
        await browser.close()

    proc.terminate()

    existing = json.loads(hashes_file.read_text()) if hashes_file.exists() else {}
    merged = {**existing, **captured}
    hashes_file.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")

    print(f"\nSaved {len(heb_cookies)} HEB cookies to {auth_path}")
    print("trust/anti-bot cookie present:", "yes" if trust else "NO — login may be incomplete")
    print(f"refreshed {len(captured)} API hashes: {sorted(captured)}")
    print('Tell the agent "done" — it will verify and run a full test.')


if __name__ == "__main__":
    asyncio.run(main())
