#!/bin/zsh
# Every 30 min (launchd StartInterval): check the MCP server's /health. On failure,
# raise a macOS notification — a dead host should never fail silently.
# Once a week it also checks PyPI for a newer texas-grocery-mcp and notifies if found
# (the pin in pyproject.toml means nothing updates without a deliberate bump).
set -u
cd "$(dirname "$0")/.."
# Load .env so the port (and any overrides) match what the server actually runs with —
# otherwise a custom MCP_HTTP_PORT would make every heartbeat a false DOWN alert.
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PORT="${MCP_HTTP_PORT:-8787}"
HEALTH=$(curl -s --max-time 10 "http://127.0.0.1:${PORT}/health")
OK=$(echo "$HEALTH" | grep -o '"ok":true' || true)

if [[ -z "$OK" ]]; then
    osascript -e "display notification \"heb-checkout health failed: ${HEALTH:-no response}\" with title \"Grocery agent DOWN\"" 2>/dev/null
    echo "$(date -Iseconds) UNHEALTHY: ${HEALTH:-no response}"
else
    echo "$(date -Iseconds) ok"
fi

# Keep the HEB session warm (refreshes only when close to expiry; ~10s when it does).
# Capture python's OWN exit code, not the pipe's — `... | tail` would mask a failure
# (and zsh has no pipefail by default), causing false "healthy" reports.
if [[ ! -x .venv/bin/python ]]; then
    osascript -e "display notification \"grocery-agent venv missing — run scripts/install.sh\" with title \"Grocery agent: setup\"" 2>/dev/null
    echo "$(date -Iseconds) SESSION: .venv/bin/python not found"
else
    KA_RAW=$(.venv/bin/python scripts/keepalive.py 2>&1); KA_RC=$?
    KA=$(echo "$KA_RAW" | tail -1)
    if [[ $KA_RC -ne 0 ]]; then
        osascript -e "display notification \"HEB session refresh failed — run capture_real_session.py\" with title \"Grocery agent: session\"" 2>/dev/null
        echo "$(date -Iseconds) SESSION: $KA"
    else
        echo "$(date -Iseconds) session: $KA"
    fi
fi

# Parked Chrome (the genuine warm browser on :9222) is the ONLY reliable session-refresh
# source — and auth.json can look fresh even when it's down. If it's unreachable the session
# will go stale and orders will fail, so alert loudly to prompt a re-login before that happens.
if ! curl -s --max-time 3 "http://127.0.0.1:9222/json/version" >/dev/null 2>&1; then
    osascript -e "display notification \"Parked Chrome is DOWN — HEB orders will fail until you run scripts/start_parked_chrome.sh and sign in.\" with title \"Grocery agent: parked Chrome\"" 2>/dev/null
    echo "$(date -Iseconds) PARKED-CHROME: down (:9222 unreachable)"
else
    echo "$(date -Iseconds) parked-chrome: up"
fi

# Weekly upstream update check (stamp file throttles to every 7 days).
STAMP=data/.last-update-check
if [[ ! -f "$STAMP" || -n "$(find "$STAMP" -mtime +7 2>/dev/null)" ]]; then
    .venv/bin/python -m heb_checkout.updates > /tmp/grocery-update-check.json 2>&1
    if [[ $? -eq 2 ]]; then
        LATEST=$(grep -o '"latest": "[^"]*"' /tmp/grocery-update-check.json | cut -d'"' -f4)
        osascript -e "display notification \"texas-grocery-mcp $LATEST is out (pinned older). Ask the agent: check for MCP updates.\" with title \"Grocery agent: upstream update\"" 2>/dev/null
        echo "$(date -Iseconds) UPDATE AVAILABLE: $LATEST"
    fi
    touch "$STAMP"
fi
