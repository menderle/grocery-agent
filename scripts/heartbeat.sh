#!/bin/zsh
# Every 30 min (launchd StartInterval): check the MCP server's /health. On failure,
# raise a macOS notification — a dead host should never fail silently.
# Once a week it also checks PyPI for a newer texas-grocery-mcp and notifies if found
# (the pin in pyproject.toml means nothing updates without a deliberate bump).
set -u
cd "$(dirname "$0")/.."
PORT="${MCP_HTTP_PORT:-8787}"
HEALTH=$(curl -s --max-time 10 "http://127.0.0.1:${PORT}/health")
OK=$(echo "$HEALTH" | grep -o '"ok":true' || true)

if [[ -z "$OK" ]]; then
    osascript -e "display notification \"heb-checkout health failed: ${HEALTH:-no response}\" with title \"Grocery agent DOWN\"" 2>/dev/null
    echo "$(date -Iseconds) UNHEALTHY: ${HEALTH:-no response}"
else
    echo "$(date -Iseconds) ok"
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
