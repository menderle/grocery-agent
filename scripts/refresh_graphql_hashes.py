"""Self-heal texas-grocery-mcp's stale GraphQL hashes (upstream issue #19).

HEB rotates its persisted-query hashes on frontend deploys, which kills the package's
cart/store operations until someone updates the hardcoded PERSISTED_QUERIES dict. This
script rediscovers them from HEB's own traffic: it drives a browser (using the saved
session) through page loads, a search, and an add-to-cart, sniffs every /graphql
request for operationName + sha256Hash, and writes them to config/graphql-hashes.json.
The shop server launcher (scripts/shop-server) applies the overrides at startup — the
installed package is never modified.

Run whenever cart/store tools start failing with 'Persisted query hash ... no longer
valid':   .venv/bin/python scripts/refresh_graphql_hashes.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright  # noqa: E402

from heb_checkout import config  # noqa: E402
from heb_checkout.browser import LAUNCH_ARGS, USER_AGENT  # noqa: E402

HASHES_JSON = Path(__file__).resolve().parents[1] / "config" / "graphql-hashes.json"
TEST_PRODUCT = "377497"  # bananas — cheap, always stocked


def extract_ops(post_data: str) -> dict[str, str]:
    found = {}
    try:
        payload = json.loads(post_data)
    except (json.JSONDecodeError, TypeError):
        return found
    for entry in payload if isinstance(payload, list) else [payload]:
        op = entry.get("operationName")
        sha = (entry.get("extensions") or {}).get("persistedQuery", {}).get("sha256Hash")
        if op and sha:
            found[op] = sha
    return found


async def harvest() -> dict[str, str]:
    auth = config.auth_state_path()
    if not auth.exists():
        sys.exit("no HEB session — run scripts/capture_real_session.py first")
    hashes: dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        ctx = await browser.new_context(user_agent=USER_AGENT, storage_state=str(auth))
        page = await ctx.new_page()
        page.on("request", lambda r: hashes.update(
            extract_ops(r.post_data) if "/graphql" in r.url and r.post_data else {}))

        async def visit(url, wait=4000):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(wait)
            except Exception as e:
                print(f"  (skip {url}: {type(e).__name__})")

        print("visiting pages to trigger GraphQL operations…")
        await visit("https://www.heb.com")
        await visit("https://www.heb.com/search?q=bananas")
        await visit(f"https://www.heb.com/product-detail/p/{TEST_PRODUCT}")
        add = page.locator("button:has-text('Add to cart')").first
        if await add.count():
            await add.click()
            await page.wait_for_timeout(4000)
            print("  clicked Add to cart")
        await visit("https://www.heb.com/cart")
        await ctx.storage_state(path=str(auth))  # keep refreshed cookies
        await browser.close()
    return hashes


def patch(hashes: dict[str, str]) -> None:
    from texas_grocery_mcp.clients.graphql import PERSISTED_QUERIES

    existing = json.loads(HASHES_JSON.read_text()) if HASHES_JSON.exists() else {}
    merged = {**existing, **hashes}
    HASHES_JSON.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")

    changed = [op for op, sha in hashes.items()
               if op in PERSISTED_QUERIES and PERSISTED_QUERIES[op] != sha]
    print(f"\ncaptured {len(hashes)} operations: {sorted(hashes)}")
    print(f"override file: {HASHES_JSON} ({len(merged)} total)")
    print(f"fresher than the package's hardcoded values: {sorted(changed) or '(none)'}")
    missing = set(PERSISTED_QUERIES) - set(merged)
    if missing:
        print(f"package ops not seen this run (package defaults still used): {sorted(missing)}")


if __name__ == "__main__":
    patch(asyncio.run(harvest()))
