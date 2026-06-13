"""Session keep-alive: refresh the HEB session when it's close to expiry. Run by the
heartbeat every 30 min so the trust tokens stay warm even when nobody is shopping.
Exit 0 = session healthy (refreshed or not needed), 1 = refresh failed."""

import asyncio
import subprocess
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"mcpServers": {"shop": {"command": str(ROOT / "scripts" / "shop-server"), "args": []}}}


def _sync_parked() -> bool:
    """Sync from the parked genuine Chrome (the reliable path). True on success."""
    r = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/sync_parked_session.py")],
                       capture_output=True, text=True, timeout=90)
    print("parked sync:", (r.stdout or r.stderr).strip())
    return r.returncode == 0


async def main() -> int:
    """Keep the HEB session warm. Primary: sync from the parked genuine Chrome
    (start_parked_chrome.sh) — reliable because a live real browser keeps Incapsula
    happy. Fallback: the package's headless auto-login (intermittently 401s). On total
    failure the heartbeat notifies; fix is re-logging into the parked window."""
    if _sync_parked():
        return 0
    async with Client(CONFIG) as c:
        d = (await c.call_tool("session_status", {})).data
        if d.get("authenticated") and not d.get("refresh_recommended"):
            print(f"session ok, {d.get('time_remaining_hours')}h remaining")
            return 0
        status = (await c.call_tool("session_refresh", {})).data.get("status", "unknown")
        print(f"fallback refresh: {status}")
        return 0 if status in ("success", "refreshed", "ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
