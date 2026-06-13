#!/bin/zsh
# Enable phone-app testing through the public Tailscale Funnel.
#
# WHY THIS EXISTS: claude.ai's custom-connector UI only supports OAuth, not our bearer
# token — so to test from the phone now, the gateway must accept the connection without
# a token. To make that SAFE, this forces HEB_CHECKOUT_DRY_RUN=true: with dry-run on,
# the worst anyone reaching the URL can do is a pretend checkout — NOTHING can be charged.
# The gateway also refuses to start no-auth if dry-run is off (hard guard in code).
#
# This is the TEMPORARY test posture. Before placing REAL orders from the phone, run
# scripts/lock_down.sh (restores token auth) and set up OAuth.
set -e
cd "$(dirname "$0")/.."

echo "Enabling phone TEST mode: open endpoint + dry-run ON (no charges possible)."
sed -i '' 's/HEB_CHECKOUT_DRY_RUN=.*/HEB_CHECKOUT_DRY_RUN=true/' .env
if grep -q MCP_ALLOW_NO_AUTH .env; then
    sed -i '' 's/MCP_ALLOW_NO_AUTH=.*/MCP_ALLOW_NO_AUTH=true/' .env
else
    echo "MCP_ALLOW_NO_AUTH=true" >> .env
fi
echo "  flags: $(grep -E 'DRY_RUN|NO_AUTH' .env | tr '\n' ' ')"

launchctl unload ~/Library/LaunchAgents/com.grocery-agent.server.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.grocery-agent.server.plist
sleep 6

echo "\nlocal health:"; curl -s http://127.0.0.1:8787/health; echo
echo "\nDone. Now click 'Add' on the connector (leave OAuth fields blank) and test from your phone."
echo "When finished testing, run:  zsh scripts/lock_down.sh"
