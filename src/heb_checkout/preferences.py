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
from .locking import prefs_lock

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


_FIELDS = ("display_name", "product_id", "sku_id", "brand", "size", "quantity",
          "substitution", "note")


def resolve(phrase: str) -> dict | None:
    """Return the remembered product for a phrase via EXACT normalized-key match.
    `_key()` already collapses filler/quantity words ("buy a 12-pack of water" -> "water",
    "add my usual water" -> "water"), so exact match handles the natural phrasings WITHOUT
    the false positives that substring/containment matching caused — e.g. a saved "water"
    must NOT silently resolve "rose water" or "sparkling water" to the wrong product, since
    recall_item feeds the matched SKU straight into a cart that may be auto-checked-out."""
    items = load().get("items", {})
    key = _key(phrase)
    if not key:
        return None
    return items.get(key)


def remember(phrase: str, *, overwrite: bool = True, **fields) -> dict:
    """Record/merge the product the user wants for a phrase. Only non-None fields are
    written, so a later call can add a brand without wiping the saved sku.

    overwrite=True (explicit user remember_item): replace fields with new values.
    overwrite=False (auto-learn from a placed order): only FILL MISSING fields — never
    clobber a value the user curated for a colliding phrase."""
    with prefs_lock():
        data = load()
        key = _key(phrase)
        if not key:
            raise ValueError("phrase is empty after normalization")
        entry = data["items"].get(key, {})
        entry.setdefault("phrase", phrase)
        for k in _FIELDS:
            v = fields.get(k)
            if v is None:
                continue
            if not overwrite and entry.get(k) not in (None, ""):
                continue  # auto-learn must not overwrite a curated value
            entry[k] = v
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data["items"][key] = entry
        save(data)
        return {"key": key, **entry}


def forget(phrase: str) -> bool:
    with prefs_lock():
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


def _staple_key(query: str) -> frozenset:
    """Order-independent token set so 'whole milk gallon' and 'gallon of whole milk' are
    the same staple."""
    return frozenset(_key(query).split())


def staples() -> list:
    return _load_staples().get("items", [])


def add_staple(query: str, quantity: int = 1, substitution: str = "ask") -> list:
    with prefs_lock():
        data = _load_staples()
        items = data.setdefault("items", [])
        target = _staple_key(query)
        for it in items:
            if _staple_key(it.get("query", "")) == target:
                it.update({"query": query, "quantity": quantity, "substitution": substitution})
                break
        else:
            items.append({"query": query, "quantity": quantity, "substitution": substitution})
        _save_staples(data)
        return items


def remove_staple(query: str) -> list:
    with prefs_lock():
        data = _load_staples()
        target = _staple_key(query)
        items = [it for it in data.get("items", []) if _staple_key(it.get("query", "")) != target]
        data["items"] = items
        _save_staples(data)
        return items
