"""Durable preferences + product memory.

Two things live in data/preferences.json:

  - general preferences: default substitution behavior, per-category brand notes, an
    "avoid" list, free-form notes.
  - an `items` map: a normalized user phrase ("my usual water", "12 pack of water")
    → the product the user actually wants (display name + HEB product_id/sku_id, plus
    brand/size/quantity/substitution). This is what lets "add my usual water" resolve to
    a specific SKU without re-asking.

Exposed as gateway tools (remember_item / recall_item / forget_item / get_preferences),
so BOTH the web UI and the Claude connector learn and recall the same picks. Reuses the
config-path + atomic-write pattern from the rest of the agent.
"""

import json
import os
import re
import tempfile
from datetime import datetime

from . import config

_DEFAULTS = {
    "_comment": "Brand/size/substitution preferences + phrase→product memory.",
    "default_substitution": "ask",
    "brands": {},
    "avoid": [],
    "notes": [],
    "items": {},
}

# Filler / quantity words dropped when keying a phrase, so "buy a 12-pack of water" and
# "add water" map to the same memory. Distinguishing words ("sparkling") are kept.
_DROP = {"a", "an", "the", "of", "my", "usual", "please", "some", "buy", "add", "get",
         "pack", "packs", "ct", "count", "case", "box", "bottle", "bottles", "can", "cans"}


def _key(phrase: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (phrase or "").lower())
    words = [w for w in s.split() if w and not w.isdigit() and w not in _DROP]
    return " ".join(words)


def load() -> dict:
    path = config.preferences_path()
    data = dict(_DEFAULTS)
    if path.exists():
        try:
            data.update(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    data.setdefault("items", {})
    return data


def save(data: dict) -> None:
    path = config.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)  # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resolve(phrase: str) -> dict | None:
    """Return the remembered product for a phrase, or None. Tries an exact normalized
    match first, then a containment match (stored key ⊆ query words, or vice versa)."""
    items = load().get("items", {})
    key = _key(phrase)
    if not key:
        return None
    if key in items:
        return items[key]
    qwords = set(key.split())
    best = None
    for k, entry in items.items():
        kwords = set(k.split())
        if kwords and (kwords <= qwords or qwords <= kwords):
            # prefer the longest (most specific) overlapping key
            if best is None or len(k) > len(best[0]):
                best = (k, entry)
    return best[1] if best else None


def remember(phrase: str, **fields) -> dict:
    """Record/merge the product the user wants for a phrase. Only non-None fields are
    written, so a later call can add a brand without wiping the saved sku."""
    data = load()
    key = _key(phrase)
    if not key:
        raise ValueError("phrase is empty after normalization")
    entry = data["items"].get(key, {})
    entry["phrase"] = phrase
    for k in ("display_name", "product_id", "sku_id", "brand", "size", "quantity",
              "substitution", "note"):
        v = fields.get(k)
        if v is not None:
            entry[k] = v
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    data["items"][key] = entry
    save(data)
    return {"key": key, **entry}


def forget(phrase: str) -> bool:
    data = load()
    key = _key(phrase)
    if key in data.get("items", {}):
        del data["items"][key]
        save(data)
        return True
    return False


def general() -> dict:
    d = load()
    return {
        "default_substitution": d.get("default_substitution", "ask"),
        "brands": d.get("brands", {}),
        "avoid": d.get("avoid", []),
        "notes": d.get("notes", []),
    }


def all_items() -> dict:
    return load().get("items", {})


# ---------- staples (the standing weekly order) ----------

def _load_staples() -> dict:
    path = config.staples_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"items": []}


def _save_staples(data: dict) -> None:
    path = config.staples_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def staples() -> list:
    return _load_staples().get("items", [])


def add_staple(query: str, quantity: int = 1, substitution: str = "ask") -> list:
    data = _load_staples()
    items = data.setdefault("items", [])
    for it in items:
        if _key(it.get("query", "")) == _key(query):
            it.update({"query": query, "quantity": quantity, "substitution": substitution})
            break
    else:
        items.append({"query": query, "quantity": quantity, "substitution": substitution})
    _save_staples(data)
    return items


def remove_staple(query: str) -> list:
    data = _load_staples()
    items = [it for it in data.get("items", []) if _key(it.get("query", "")) != _key(query)]
    data["items"] = items
    _save_staples(data)
    return items
