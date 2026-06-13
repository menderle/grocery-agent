"""Session keep-alive: refresh the HEB session when it's close to expiry. Run by the
heartbeat every 30 min so the trust tokens stay warm even when nobody is shopping.
Exit 0 = session healthy (refreshed or not needed), 1 = refresh failed."""

import asyncio
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"mcpServers": {"shop": {"command": str(ROOT / ".venv" / "bin" / "texas-grocery-mcp"), "args": []}}}


async def main() -> int:
    async with Client(CONFIG) as c:
        r = await c.call_tool("session_status", {})
        d = r.data
        hours = d.get("time_remaining_hours")
        if d.get("authenticated") and not d.get("refresh_recommended"):
            print(f"session ok, {hours}h remaining")
            return 0
        r = await c.call_tool("session_refresh", {})
        status = r.data.get("status", "unknown")
        print(f"refresh: {status}")
        return 0 if status in ("success", "refreshed", "ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
