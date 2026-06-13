#!/bin/zsh
# Restore the secure posture after phone testing: re-enable bearer-token auth and keep
# dry-run ON. After this, the public endpoint rejects anything without your token again.
set -e
cd "$(dirname "$0")/.."

echo "Locking down: bearer-token auth ON, dry-run ON."
sed -i '' 's/MCP_ALLOW_NO_AUTH=.*/MCP_ALLOW_NO_AUTH=false/' .env 2>/dev/null || true
sed -i '' 's/HEB_CHECKOUT_DRY_RUN=.*/HEB_CHECKOUT_DRY_RUN=true/' .env

launchctl unload ~/Library/LaunchAgents/com.grocery-agent.server.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.grocery-agent.server.plist
sleep 6
echo "health:"; curl -s http://127.0.0.1:8787/health; echo
echo "Locked down. The phone connector will stop working until we set up OAuth (the"
echo "real fix for authenticated phone access)."
