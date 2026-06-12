#!/bin/zsh
# Bundle everything a new host needs: config, data (minus screenshots), and the
# shared HEB session. Output: grocery-agent-snapshot.tar.gz in the repo root.
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

cp -R config "$STAGE/config"
rsync -a --exclude 'orders/screenshots' data/ "$STAGE/data/"
AUTH_DIR="${AUTH_STATE_DIR:-$HOME/.texas-grocery-mcp}"
[ -d "$AUTH_DIR" ] && cp -R "$AUTH_DIR" "$STAGE/auth-state"

tar czf grocery-agent-snapshot.tar.gz -C "$STAGE" .
echo "wrote grocery-agent-snapshot.tar.gz ($(du -h grocery-agent-snapshot.tar.gz | cut -f1))"
