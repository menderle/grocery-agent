HOME_DIR := $(shell pwd)
SNAPSHOT := grocery-agent-snapshot.tar.gz
AUTH_STATE := $(HOME)/.texas-grocery-mcp

.PHONY: selftest serve serve-http snapshot restore migrate install-launchd uninstall-launchd

selftest:           ## policy/audit/approvals checks — no network, no browser
	.venv/bin/python scripts/selftest.py

serve:              ## stdio MCP server (local Claude Code/Desktop)
	.venv/bin/heb-checkout

serve-http:         ## HTTP MCP server for the tunnel (needs MCP_BEARER_TOKEN in env/.env)
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/heb-checkout --http

snapshot:           ## tar config + data + HEB session for migration to another host
	zsh scripts/snapshot.sh

restore:            ## unpack a snapshot on a new host (run from the repo root there)
	zsh scripts/restore.sh

migrate: snapshot   ## alias: produce the snapshot + print the runbook
	@echo "1. scp $(SNAPSHOT) newhost:~/grocery-agent/"
	@echo "2. on new host: git clone <this repo> && cd grocery-agent && make restore"
	@echo "3. Mac: uv venv && uv pip install -e . 'texas-grocery-mcp[browser]' && make install-launchd"
	@echo "   Linux/VPS: cp config/.env.example .env (fill tokens) && docker compose up -d"
	@echo "4. repoint the cloudflared tunnel at the new host; phone connector keeps working"

install-launchd:    ## register the HTTP server + heartbeat as LaunchAgents (this Mac)
	mkdir -p ~/Library/LaunchAgents
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.maurice.heb-checkout.plist > ~/Library/LaunchAgents/com.maurice.heb-checkout.plist
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.maurice.grocery-heartbeat.plist > ~/Library/LaunchAgents/com.maurice.grocery-heartbeat.plist
	launchctl unload ~/Library/LaunchAgents/com.maurice.heb-checkout.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.maurice.heb-checkout.plist
	launchctl unload ~/Library/LaunchAgents/com.maurice.grocery-heartbeat.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.maurice.grocery-heartbeat.plist
	@echo "LaunchAgents installed. Logs: /tmp/heb-checkout.log /tmp/grocery-heartbeat.log"

uninstall-launchd:
	launchctl unload ~/Library/LaunchAgents/com.maurice.heb-checkout.plist 2>/dev/null || true
	launchctl unload ~/Library/LaunchAgents/com.maurice.grocery-heartbeat.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.maurice.heb-checkout.plist ~/Library/LaunchAgents/com.maurice.grocery-heartbeat.plist
