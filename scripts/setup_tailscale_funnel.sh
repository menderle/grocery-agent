#!/bin/zsh
# Expose the grocery gateway (localhost:8787) on a STABLE public Tailscale Funnel URL,
# so the Claude phone app's custom connector can reach it. Run AFTER you've installed
# Tailscale and signed in (`tailscale up`). Idempotent.
#
#   zsh scripts/setup_tailscale_funnel.sh
set -e
PORT=8787

# Find the tailscale CLI (GUI app bundle or a PATH install).
TS=""
for cand in \
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale" \
    "$(command -v tailscale 2>/dev/null)" \
    "$HOME/.local/bin/tailscale"; do
    [ -n "$cand" ] && [ -x "$cand" ] && TS="$cand" && break
done
if [ -z "$TS" ]; then
    echo "✗ Tailscale CLI not found. Install the Tailscale app and sign in first."
    exit 1
fi
echo "using tailscale: $TS"

# Must be logged in.
if ! "$TS" status >/dev/null 2>&1; then
    echo "✗ Not logged in. Run:  $TS up   (opens a browser to sign in), then re-run me."
    exit 1
fi

# Confirm the gateway is actually up locally before exposing it.
if ! curl -s --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null; then
    echo "✗ Gateway not responding on :$PORT — start it (make install-launchd) first."
    exit 1
fi

echo "enabling Funnel for localhost:$PORT (background/persistent)…"
# First run may print a link to enable the Funnel feature in the admin console — follow
# it once, approve, then re-run this script.
"$TS" funnel --bg "$PORT"

echo
echo "Funnel status:"
"$TS" funnel status || true

# Derive the public base URL from the node's DNS name.
HOST=$("$TS" status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
if [ -n "$HOST" ]; then
    echo
    echo "================================================================"
    echo "Your connector URL (add this in the Claude app):"
    echo "    https://$HOST/mcp"
    echo "Auth header:  Bearer <your MCP_BEARER_TOKEN>"
    echo "  (get it:  grep MCP_BEARER_TOKEN $(cd "$(dirname "$0")/.." && pwd)/.env )"
    echo "================================================================"
fi
