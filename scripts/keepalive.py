"""Session keep-alive: refresh the HEB session when it's close to expiry. Run by the
heartbeat every 30 min so the trust tokens stay warm even when nobody is shopping.
Exit 0 = session healthy (refreshed or not needed), 1 = refresh failed."""

import asyncio
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"mcpServers": {"shop": {"command": str(ROOT / "scripts" / "shop-server"), "args": []}}}


async def main() -> int:
    """Keep the HEB session warm. session_refresh auto-logs-in with the saved Keychain
    credentials; it works most of the time but HEB's Incapsula intermittently 401s it.
    On failure the caller (heartbeat) notifies and the fix is scripts/capture_real_session.py.
    Retried every 30 min, a transient 401 usually clears on the next tick."""
    async with Client(CONFIG) as c:
        d = (await c.call_tool("session_status", {})).data
        if d.get("authenticated") and not d.get("refresh_recommended"):
            print(f"session ok, {d.get('time_remaining_hours')}h remaining")
            return 0
        status = (await c.call_tool("session_refresh", {})).data.get("status", "unknown")
        print(f"refresh: {status}")
        return 0 if status in ("success", "refreshed", "ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
