"""End-to-end dry-run test against live HEB. Proves the whole agent without spending
a cent: search → cart → slots → preview → policy approval → dry-run place (stops one
click before purchase) → audit → cleanup. Leaves the cart exactly as found (empty).

Run:  make e2e   (or .venv/bin/python scripts/e2e_test.py)
Requires: HEB session (capture_real_session.py) and HEB_CHECKOUT_DRY_RUN=true (default).
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastmcp import Client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHOP = {"mcpServers": {"shop": {"command": str(ROOT / "scripts" / "shop-server"), "args": []}}}
TEST_QUERIES = ("hill country fare large white eggs", "fresh bananas")


async def main() -> int:
    import os
    if os.environ.get("HEB_CHECKOUT_DRY_RUN", "true").lower() in ("false", "0", "no"):
        sys.exit("refusing to run e2e with dry-run disabled — this test must never charge")
    os.environ.setdefault("GROCERY_AGENT_HOME", str(ROOT))

    t0 = time.time()
    step = lambda s: print(f"\n[{time.time()-t0:5.1f}s] {s}")  # noqa: E731
    ok = lambda s: print(f"  ✓ {s}")  # noqa: E731

    async with Client(SHOP) as shop:
        step("1. session check")
        d = (await shop.call_tool("session_status", {})).data
        assert d.get("authenticated"), f"not authenticated — run capture_real_session.py ({d.get('message')})"
        ok(f"authenticated, {d.get('time_remaining_hours')}h left")

        step("2. cart must start empty (test won't touch a real in-progress cart)")
        d = (await shop.call_tool("cart_get", {})).data
        assert d.get("item_count") == 0, f"