"""Ensure a LIVE HEB session before a shopping flow. The package's per-call auto-refresh
intermittently 401s (Incapsula); a transient 401 almost always clears on a retry a few
seconds later. This retries refresh until a real cart_get returns 200 (proof the session
actually works, not just that refresh claimed success), or gives up after N attempts.

Use at the start of any unattended flow:  .venv/bin/python scripts/ensure_session.py
Exit 0 = session proven live, 1 = needs manual capture_real_session.py."""

import asyncio
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
SHOP = {"mcpServers": {"shop": {"command": str(ROOT / "scripts" / "shop-server"), "args": []}}}
ATTEMPTS = 5


async def _cart_ok(c) -> bool:
    d = (await c.call_tool("cart_get", {})).data
    return not d.get("error")  # 401 surfaces as error=True


async def main() -> int:
    async with Client(SHOP) as c:
        for i in range(1, ATTEMPTS + 1):
            if await _cart_ok(c):
                print(f"session live (attempt {i})")
                return 0
            print(f"attempt {i}: cart blocked, refreshing…")
            await c.call_tool("session_refresh", {})
            await asyncio.sleep(4)
        print("session still blocked after retries — run scripts/capture_real_session.py")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
