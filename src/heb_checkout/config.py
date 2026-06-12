"""Paths and environment for the grocery agent. Everything hangs off GROCERY_AGENT_HOME
so the whole stack can move hosts by copying one directory."""

import os
from pathlib import Path


def agent_home() -> Path:
    return Path(os.environ.get("GROCERY_AGENT_HOME", "~/Claude/grocery-agent")).expanduser()


def policy_path() -> Path:
    return agent_home() / "config" / "policy.yaml"


def orders_dir() -> Path:
    d = agent_home() / "data" / "orders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def approvals_path() -> Path:
    return agent_home() / "data" / "approvals.json"


def auth_state_path() -> Path:
    # Shared with texas-grocery-mcp (its AUTH_STATE_PATH setting).
    return Path(os.environ.get("AUTH_STATE_PATH", "~/.texas-grocery-mcp/auth.json")).expanduser()


def dry_run_default() -> bool:
    return os.environ.get("HEB_CHECKOUT_DRY_RUN", "true").lower() not in ("false", "0", "no")
