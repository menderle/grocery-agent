"""Paths and environment for the grocery agent. Everything hangs off GROCERY_AGENT_HOME
so the whole stack can move hosts by copying one directory."""

import os
from pathlib import Path


def agent_home() -> Path:
    # Prefer GROCERY_AGENT_HOME; otherwise derive from this file's location so a fresh clone
    # works anywhere (src/heb_checkout/config.py -> repo root) with no per-user path baked in.
    env = os.environ.get("GROCERY_AGENT_HOME")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load agent_home()/.env into os.environ (without overriding existing values).
    Stdio MCP servers and launchd jobs don't inherit a shell that sourced .env, so
    secrets (API tokens, ICS URLs, bearer token) are loaded here at import time."""
    env_file = agent_home() / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_env()


def policy_path() -> Path:
    return agent_home() / "config" / "policy.yaml"


def orders_dir() -> Path:
    d = agent_home() / "data" / "orders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def approvals_path() -> Path:
    return agent_home() / "data" / "approvals.json"


def preferences_path() -> Path:
    """Durable brand/size/substitution preferences + the phrase→product memory map
    ("my usual water" → a specific SKU). Shared by every interface."""
    return agent_home() / "data" / "preferences.json"


def staples_path() -> Path:
    """The standing weekly order (items the agent re-buys)."""
    return agent_home() / "data" / "staples.json"


def checkout_lock_path() -> Path:
    """Cross-process mutex file for the place-order critical section, so the web UI
    and the Claude connector (two gateway instances) can't double-place."""
    return agent_home() / "data" / ".checkout.lock"


def prefs_lock_path() -> Path:
    """Cross-process mutex for preferences/staples read-modify-write. Separate from the
    checkout lock so memory writes never serialize against (or stall) checkout."""
    return agent_home() / "data" / ".preferences.lock"


def auth_state_path() -> Path:
    # Shared with texas-grocery-mcp (its AUTH_STATE_PATH setting).
    return Path(os.environ.get("AUTH_STATE_PATH", "~/.texas-grocery-mcp/auth.json")).expanduser()


def dry_run_default() -> bool:
    return os.environ.get("HEB_CHECKOUT_DRY_RUN", "true").lower() not in ("false", "0", "no")


# ---------- Favor (on-demand delivery) — separate account/session from HEB ----------

def favor_auth_state_path() -> Path:
    return Path(os.environ.get(
        "FAVOR_AUTH_STATE_PATH", str(agent_home() / "state" / "favor-auth.json"))).expanduser()



def favor_default_address() -> str:
    return os.environ.get("FAVOR_DEFAULT_ADDRESS", "")


def favor_dry_run_default() -> bool:
    return os.environ.get("FAVOR_CHECKOUT_DRY_RUN", "true").lower() not in ("false", "0", "no")
