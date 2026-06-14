#!/bin/zsh
# Park a genuine Chrome on the FAVOR profile (separate from the HEB one) with a debug
# port, and leave it running. Same reliable-session approach as HEB, on a different
# profile + port (9223) so the two never collide.
#
# Run once:  zsh scripts/start_parked_favor_chrome.sh  → log into Favor, leave it open.
set -e
PORT=9223
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$ROOT/profiles/favor-park"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "parked Favor Chrome already running on port $PORT — nothing to do."
    exit 0
fi

mkdir -p "$PROFILE"
nohup "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check \
    "https://www.favordelivery.com/login/" >/dev/null 2>&1 &

echo "Parked Favor Chrome launched (port $PORT, profile favor-park)."
echo "  1. Log in to your FAVOR account (phone number + SMS code — separate from HEB)."
echo "  2. Set your delivery address (the H-E-B Now storefront should load)."
echo "  3. Leave the window open (minimized is fine)."
echo 'Then run: .venv/bin/python scripts/sync_parked_favor_session.py'
