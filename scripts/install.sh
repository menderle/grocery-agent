#!/bin/zsh
# One-command setup on any Mac (Linux works for the Docker path; see docker-compose.yml).
# Idempotent — safe to re-run after git pull or on a migrated host.
#
#   git clone <repo> grocery-agent && cd grocery-agent && zsh scripts/install.sh

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "==> grocery-agent installer ($ROOT)"

# 1. uv (user-local, no admin) — manages Python so the system version doesn't matter
if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv (Python manager) to ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# 2. venv + pinned dependencies
[ -d .venv ] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
echo "==> installing Chromium for Playwright (~100MB, first run only)"
.venv/bin/playwright install chromium

# 3. secrets — generated, never committed
if [ ! -f .env ]; then
    TOKEN=$(openssl rand -hex 32)
    sed "s/change-me-long-random-string/$TOKEN/" config/.env.example > .env
    echo "==> wrote .env with a fresh LIST_DROP_TOKEN (set OAUTH_* for phone access — see SETUP.md)"
fi

# 3b. per-user home store (gitignored; set yours before first order)
if [ ! -f config/store.json ]; then
    cp config/store.json.example config/store.json
    echo "==> wrote config/store.json — EDIT IT with your store (ask the agent 'search HEB stores near <address>')"
fi

# 3c. optional local web UI note (additive; the Claude/MCP path needs none of this)
if ! grep -q '^ANTHROPIC_API_KEY=' .env 2>/dev/null; then
    echo "==> (optional) Local web UI: add ANTHROPIC_API_KEY to .env, then run 'make web' (see docs/WEB-UI.md)"
fi

# 4. MCP registration for this machine's absolute paths (Claude Code/Desktop pick up
#    .mcp.json from the project root; other clients: see docs/INTEGRATION.md)
cat > .mcp.json <<EOF
{
  "mcpServers": {
    "texas-grocery": {
      "command": "$ROOT/scripts/shop-server",
      "args": [],
      "env": {}
    },
    "heb-checkout": {
      "command": "$ROOT/.venv/bin/heb-checkout",
      "args": [],
      "env": {
        "GROCERY_AGENT_HOME": "$ROOT",
        "HEB_CHECKOUT_DRY_RUN": "true"
      }
    }
  }
}
EOF
echo "==> wrote .mcp.json for this machine"

# 5. prove the safety layer works before anything touches a browser
make selftest

cat <<'EOF'

Install complete. Next steps (SETUP.md has details):
  1. HEB account + one manual curbside order        [human]
  2. Register prepaid card at issuer, then:
       .venv/bin/python scripts/add_card.py          [human]
  3. Open your LLM client in this folder and say "authenticate with HEB"
  4. Verify checkout in dry-run mode before anything live
  5. Always-on:  make install-launchd     Phone/remote:  docs/INTEGRATION.md
  6. Optional local web UI:  add ANTHROPIC_API_KEY to .env, then  make web   (docs/WEB-UI.md)
EOF
