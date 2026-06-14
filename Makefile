HOME_DIR := $(shell pwd)
SNAPSHOT := grocery-agent-snapshot.tar.gz
AUTH_STATE := $(HOME)/.texas-grocery-mcp

.PHONY: selftest serve serve-http snapshot restore migrate install-launchd uninstall-launchd

selftest:           ## policy/audit/approvals checks — no network, no browser
	.venv/bin/python scripts/selftest.py

serve:              ## stdio MCP server (local Claude Code/Desktop)
	.venv/bin/heb-checkout

serve-http:         ## full gateway (shop+checkout) over HTTP for the tunnel/remote clients
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/grocery-gateway --http

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

install:            ## one-command setup on a new machine (or re-run anytime; idempotent)
	zsh scripts/install.sh

install-launchd:    ## register the HTTP server + heartbeat as LaunchAgents (this Mac)
	mkdir -p ~/Library/LaunchAgents
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.grocery-agent.server.plist > ~/Library/LaunchAgents/com.grocery-agent.server.plist
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.grocery-agent.heartbeat.plist > ~/Library/LaunchAgents/com.grocery-agent.heartbeat.plist
	launchctl unload ~/Library/LaunchAgents/com.grocery-agent.server.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.grocery-agent.server.plist
	launchctl unload ~/Library/LaunchAgents/com.grocery-agent.heartbeat.plist 2>/dev/null || true
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.grocery-agent.session-sync.plist > ~/Library/LaunchAgents/com.grocery-agent.session-sync.plist
	launchctl unload ~/Library/LaunchAgents/com.grocery-agent.session-sync.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.grocery-agent.session-sync.plist
	sed "s|__HOME__|$(HOME_DIR)|g" deploy/launchd/com.grocery-agent.parked-chrome.plist > ~/Library/LaunchAgents/com.grocery-agent.parked-chrome.plist
	launchctl unload ~/Library/LaunchAgents/com.grocery-agent.parked-chrome.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.grocery-agent.parked-chrome.plist
	launchctl load ~/Library/LaunchAgents/com.grocery-agent.heartbeat.plist
	@echo "LaunchAgents installed (server, heartbeat, session-sync, parked-chrome)."

uninstall-launchd:
	for j in server heartbeat session-sync parked-chrome; do \
		launchctl unload ~/Library/LaunchAgents/com.grocery-agent.$$j.plist 2>/dev/null || true; \
		rm -f ~/Library/LaunchAgents/com.grocery-agent.$$j.plist; \
	done

favor-login:        ## one-time Favor login (Playwright persistent profile; incl. SMS)
	@echo "Favor uses a persistent Playwright profile, not launchd. Run once:"
	@echo "  .venv/bin/python scripts/favor_persistent_login.py   (log in + pass SMS)"
	@echo "then set FAVOR_DEFAULT_ADDRESS in .env. Favor is semi-automated: the agent"
	@echo "builds the cart; you place it in the Favor app (SMS verification at checkout)."
