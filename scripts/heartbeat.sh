#!/bin/zsh
# Every 30 min (launchd StartInterval): check the MCP server's /health. On failure,
# raise a macOS notification — a dead host should never fail silently.
set -u
PORT="${MCP_HTTP_PORT:-8787}"
HEALTH=$(curl -s --max-time 10 "http://127.0.0.1:${PORT}/health")
OK=$(echo "$HEALTH" | grep -o '"ok":true' || true)

if [[ -z "$OK" ]]; then
    osascript -e "display notification \"heb-checkout health failed: ${HEALTH:-no response}\" with title \"Grocery agent DOWN\"" 2>/dev/null
    echo "$(date -Iseconds) UNHEALTHY: ${HEALTH:-no response}"
else
    echo "$(date -Iseconds) ok"
fi
