#!/bin/zsh
# Restore a snapshot on a new host. Run from the repo root with the tarball present.
set -euo pipefail
cd "$(dirname "$0")/.."

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
tar xzf grocery-agent-snapshot.tar.gz -C "$STAGE"

rsync -a "$STAGE/config/" config/
rsync -a "$STAGE/data/" data/
if [ -d "$STAGE/auth-state" ]; then
    AUTH_DIR="${AUTH_STATE_DIR:-$HOME/.texas-grocery-mcp}"
    mkdir -p "$AUTH_DIR"
    rsync -a "$STAGE/auth-state/" "$AUTH_DIR/"
fi
echo "restored config/, data/, and HEB session."
echo "next (Mac):   make install-launchd"
echo "next (Linux): cp config/.env.example .env && docker compose up -d"
