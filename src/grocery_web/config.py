"""Web-only configuration. Reuses heb_checkout.config for everything shared (paths,
.env loading). Importing heb_checkout.config runs load_env(), so ANTHROPIC_API_KEY and
the WEB_* vars from .env are in os.environ here."""

import os

from heb_checkout import config as core  # noqa: F401  (import runs load_env())

# Friendly alias → exact model id (no date suffixes — current API uses bare aliases).
MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5",
}
MODEL_LABELS = {
    "sonnet": "Sonnet 4.6 · fast & economical",
    "opus": "Opus 4.8 · most capable",
    "haiku": "Haiku 4.5 · cheapest",
}


def anthropic_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def web_port() -> int:
    return int(os.environ.get("WEB_PORT", "8788"))


def web_bind() -> str:
    return os.environ.get("WEB_BIND", "127.0.0.1")


def web_auth_token() -> str:
    return os.environ.get("WEB_AUTH_TOKEN", "").strip()


def allowed_aliases() -> list[str]:
    raw = os.environ.get("GROCERY_WEB_MODELS", "sonnet,opus,haiku")
    aliases = [a.strip() for a in raw.split(",") if a.strip() in MODEL_ALIASES]
    return aliases or ["sonnet"]


def default_alias() -> str:
    want = os.environ.get("GROCERY_WEB_MODEL", "sonnet").strip()
    # accept either an alias or a full model id
    if want in MODEL_ALIASES:
        alias = want
    else:
        alias = next((a for a, mid in MODEL_ALIASES.items() if mid == want), "sonnet")
    return alias if alias in allowed_aliases() else allowed_aliases()[0]


def resolve_model(alias_or_id: str | None) -> str:
    """Map a UI selection to a concrete model id, constrained to the allow-list.
    Falls back to the default when the selection is unknown/not allowed."""
    allowed = allowed_aliases()
    if alias_or_id in MODEL_ALIASES and alias_or_id in allowed:
        return MODEL_ALIASES[alias_or_id]
    # a full id that corresponds to an allowed alias is fine too
    for a in allowed:
        if MODEL_ALIASES[a] == alias_or_id:
            return MODEL_ALIASES[a]
    return MODEL_ALIASES[default_alias()]


def model_choices() -> list[dict]:
    """For the UI picker: [{alias, id, label}], default first."""
    aliases = allowed_aliases()
    default = default_alias()
    ordered = [default] + [a for a in aliases if a != default]
    return [{"alias": a, "id": MODEL_ALIASES[a], "label": MODEL_LABELS[a]} for a in ordered]


def conversations_dir():
    d = core.agent_home() / "data" / "web-conversations"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Tools the autonomous web agent loop must NOT be able to call — money-safety enforced in
# CODE, not the system prompt. set_policy could raise its own spend caps / switch to
# full_auto; the wallet mutators change payment methods. Prompt-injected tool content
# (e.g. a crafted HEB product name) therefore cannot escalate. The Claude connector
# (human-in-the-loop, real-time) keeps these; only the web loop is denied them.
def denied_tools() -> set[str]:
    raw = os.environ.get(
        "GROCERY_WEB_TOOL_DENY", "set_policy,update_payment_card,remove_payment_card")
    return {t.strip() for t in raw.split(",") if t.strip()}
