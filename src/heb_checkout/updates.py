"""Check PyPI for newer releases of the pinned upstream MCP. The pin in pyproject.toml
means nothing updates automatically — this only *reports*, so upgrades stay deliberate:
review the release, bump the pin, reinstall, re-run make selftest.

CLI: `python -m heb_checkout.updates` prints JSON; exit code 2 when an update exists
(used by scripts/heartbeat.sh for the weekly notification)."""

import json
import sys
import urllib.request
from importlib.metadata import version

PACKAGE = "texas-grocery-mcp"
RELEASES_URL = "https://github.com/mgwalkerjr95/texas-grocery-mcp/releases"


def check() -> dict:
    installed = version(PACKAGE)
    with urllib.request.urlopen(f"https://pypi.org/pypi/{PACKAGE}/json", timeout=15) as r:
        latest = json.load(r)["info"]["version"]
    return {
        "package": PACKAGE,
        "installed": installed,
        "latest": latest,
        "update_available": latest != installed,
        "how_to_upgrade": (
            f"Review {RELEASES_URL}, then change the pin in pyproject.toml to =={latest}, "
            "run: uv pip install --python .venv/bin/python -e . && make selftest"
        ) if latest != installed else None,
    }


if __name__ == "__main__":
    try:
        result = check()
    except Exception as e:  # offline etc. — report, don't crash the heartbeat
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    print(json.dumps(result, indent=2))
    sys.exit(2 if result["update_available"] else 0)
