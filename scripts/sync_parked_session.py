"""Sync the HEB session FROM the parked genuine Chrome (scripts/start_parked_chrome.sh)
into auth.json. CDP-connects to the live browser, nudges it to heb.com to renew the
reese84 trust token, reads its storage_state, and also harvests any GraphQL hashes seen.
Because the browser is genuine and continuously running, this is the reliable refresh —
no cold re-login, nothing for Incapsula to block.

Exit 0 = synced a live authenticated session, 1 = parked Chrome missing or logged out."""

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402

PORT = 9222
HASHES_FILE = config.agent_home() / "config" / "graphql-hashes.json"


def _parked_up() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False


async def main() -> int:
    if not _parked_up():
        print("parked Chrome not running — start it with scripts/start_parked_chrome.sh")
        return 1

    captured: dict[str, str] = {}

    def sniff(req):
        if "/graphql" in req.url and req.post_data:
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
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        page.on("request", sniff)
        try:
            await page.goto("https://www.heb.com", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)  # renew reese84
        except Exception as e:
            print(f"nudge failed ({type(e).__name__}) — reading cookies anyway")
        state = await ctx.storage_state()
        await page.close()
        await browser.close()

    heb = [c for c in state.get("cookies", []) if "heb.com" in c.get("domain", "")]
    names = {c["name"] for c in heb}
    if not {"sat", "sst"} & names:
        print("parked Chrome is logged OUT — log in again in that window")
        return 1

    config.auth_state_path().write_text(json.dumps(state))
    if captured:
        existing = json.loads(HASHES_FILE.read_text()) if HASHES_FILE.exists() else {}
        HASHES_FILE.write_text(json.dumps({**existing, **captured}, indent=2, sort_keys=True) + "\n")
    print(f"synced {len(heb)} cookies from parked Chrome (+{len(captured)} hashes)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
