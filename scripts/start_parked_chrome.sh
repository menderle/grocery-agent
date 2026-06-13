#!/bin/zsh
# Park a genuine Chrome on the HEB profile with a debug port and LEAVE IT RUNNING.
# A continuously-running real browser keeps HEB's Incapsula session alive — it's the
# cold relaunch that gets blocked, not a live session. The agent CDP-reads cookies from
# this window (scripts/sync_parked_session.py). On a Mac mini this just sits there 24/7.
#
# Run once:  zsh scripts/start_parked_chrome.sh   → log in, then leave the window open.
set -e
PORT=9222
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$ROOT/profiles/heb-park"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "parked Chrome already running on port $PORT — nothing to do."
    exit 0
fi

mkdir -p "$PROFILE"
nohup "$CHROME" --remote-debugging-port=$PORT --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check \
    "https://www.heb.com/my-account/login" >/dev/null 2>&1 &

echo "Parked Chrome launched (port $PORT, profile heb-park)."
echo "  1. Log in to HEB in that window — CHECK 'keep me signed in' if offered."
echo "  2. Set your home store if asked."
echo "  3. Leave the window open (you can minimize it)."
echo 'Then tell the agent "done".'
