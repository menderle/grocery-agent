"""Grocery-list intake. Pluggable sources configured in config/lists.yaml:

  apple_notes      — macOS Notes note (AppleScript), read + mark-handled
  apple_reminders  — macOS Reminders list (AppleScript; Siri-fed), read + complete
  google_doc       — any "Anyone with link – Viewer" Google Doc/Sheet, read-only
  inbox_file       — plain text file; universal fallback (file sync, scripts, the
                     gateway's POST /list endpoint, other agents)

Every source degrades gracefully: unavailable (wrong OS, no config) is reported as
such, never raised — one broken source must not hide the others."""

import html as html_lib
import platform
import re
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

import yaml

from . import config

HANDLED_MARK = "✓"


def _cfg() -> dict:
    path = config.agent_home() / "config" / "lists.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def parse_items(text: str) -> list[str]:
    """Lines → grocery items: strips bullets/checkboxes, skips blanks, headings
    (lines ending ':'), already-handled lines (✓ prefix), and title-ish lines."""
    items = []
    for line in text.splitlines():
        s = line.strip()
        s = re.sub(r"^[-*•·]+\s*", "", s)
        s = re.sub(r"^\[\s*[xX]?\s*\]\s*", "", s)
        s = re.sub(r"^[☐☑✅]\s*", "", s).strip()
        if not s or s.endswith(":") or s.startswith(HANDLED_MARK):
            continue
        if s.lower() in ("groceries", "grocery list", "shopping list", "list"):
            continue
        items.append(s)
    return items


# ---------- AppleScript sources (macOS) ----------

def _osascript(script: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def read_apple_notes(note_title: str) -> dict:
    body = _osascript(
        f'tell application "Notes" to get body of note "{note_title}"'
    )
    if body is None:
        return {"available": False,
                "hint": f'no Notes note titled "{note_title}" (or not on macOS)'}
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", body))
    return {"available": True, "items": parse_items(text)}


def mark_apple_notes_handled(note_title: str, items: list[str]) -> bool:
    """Rewrite the note with handled items prefixed by ✓ and a dated footer."""
    body = _osascript(f'tell application "Notes" to get body of note "{note_title}"')
    if body is None:
        return False
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", body))
    handled = {i.lower() for i in items}
    out_lines = []
    for line in text.splitlines():
        s = line.strip()
        core = parse_items(s)  # normalizes one line; empty if heading/blank/handled
        if core and core[0].lower() in handled:
            out_lines.append(f"{HANDLED_MARK} {core[0]}")
        elif s:
            out_lines.append(s)
    out_lines.append(f"({HANDLED_MARK} = ordered {date.today().isoformat()})")
    new_body = "".join(f"<div>{html_lib.escape(line)}</div>" for line in out_lines)
    return _osascript(
        f'tell application "Notes" to set body of note "{note_title}" to "{new_body}"'
    ) is not None


def read_apple_reminders(list_name: str) -> dict:
    out = _osascript(
        'set AppleScript\'s text item delimiters to linefeed\n'
        f'tell application "Reminders" to get (name of reminders of list "{list_name}" '
        'whose completed is false) as text'
    )
    if out is None:
        return {"available": False,
                "hint": f'no Reminders list named "{list_name}" (or not on macOS)'}
    return {"available": True, "items": [s for s in out.splitlines() if s.strip()]}


def complete_apple_reminders(list_name: str, items: list[str]) -> bool:
    handled = "{" + ", ".join(f'"{i}"' for i in items) + "}"
    return _osascript(
        f'set handledNames to {handled}\n'
        'tell application "Reminders"\n'
        f'  repeat with r in (reminders of list "{list_name}" whose completed is false)\n'
        '    if handledNames contains (name of r as text) then set completed of r to true\n'
        '  end repeat\n'
        'end tell'
    ) is not None


# ---------- Google Doc / Sheet (link-shared, read-only, no OAuth) ----------

def _gdoc_export_url(url: str) -> str | None:
    m = re.search(r"docs\.google\.com/document/d/([\w-]+)", url)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if m:
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    return None


def read_google_doc(url: str) -> dict:
    export = _gdoc_export_url(url)
    if not export:
        return {"available": False, "hint": "URL is not a Google Doc/Sheet link"}
    req = urllib.request.Request(export, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            final_url = r.geturl()
            if "accounts.google.com" in final_url:
                raise PermissionError
            text = r.read().decode("utf-8", errors="replace")
    except Exception:
        return {
            "available": False,
            "hint": "could not read the doc — set sharing to "
                    "'Anyone with the link – Viewer' (read-only access, no login)",
        }
    if export.endswith("format=csv"):
        text = "\n".join(line.split(",")[0].strip().strip('"') for line in text.splitlines())
    return {"available": True, "items": parse_items(text), "read_only": True}


# ---------- Inbox file (universal) ----------

def _inbox_path(cfg: dict) -> Path:
    rel = (cfg.get("inbox_file") or {}).get("path", "data/inbox.md")
    return config.agent_home() / rel


def read_inbox(cfg: dict) -> dict:
    path = _inbox_path(cfg)
    if not path.exists():
        return {"available": True, "items": []}
    return {"available": True, "items": parse_items(path.read_text())}


def append_inbox(text: str) -> int:
    """Used by the gateway's POST /list endpoint (Shortcuts, webhooks, other agents)."""
    cfg = _cfg()
    path = _inbox_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = parse_items(text)
    with path.open("a") as f:
        for item in items:
            f.write(item + "\n")
    return len(items)


def clear_inbox(cfg: dict) -> bool:
    path = _inbox_path(cfg)
    if path.exists():
        path.write_text("")
    return True


# ---------- Facade ----------

def read_all() -> dict:
    cfg = _cfg()
    sources: dict[str, dict] = {}
    notes_cfg = cfg.get("apple_notes") or {}
    if notes_cfg.get("enabled", True):
        sources["apple_notes"] = read_apple_notes(notes_cfg.get("note_title", "Groceries"))
    rem_cfg = cfg.get("apple_reminders") or {}
    if rem_cfg.get("enabled", True):
        sources["apple_reminders"] = read_apple_reminders(rem_cfg.get("list_name", "Groceries"))
    gdoc_url = (cfg.get("google_doc") or {}).get("url") or ""
    if gdoc_url:
        sources["google_doc"] = read_google_doc(gdoc_url)
    sources["inbox_file"] = read_inbox(cfg)

    merged, seen = [], set()
    for data in sources.values():
        for item in data.get("items", []):
            if item.lower() not in seen:
                seen.add(item.lower())
                merged.append(item)
    return {"sources": sources, "merged_items": merged}


def clear(source: str, items: list[str]) -> dict:
    cfg = _cfg()
    if source == "apple_notes":
        ok = mark_apple_notes_handled(
            (cfg.get("apple_notes") or {}).get("note_title", "Groceries"), items)
        return {"source": source, "cleared": ok}
    if source == "apple_reminders":
        ok = complete_apple_reminders(
            (cfg.get("apple_reminders") or {}).get("list_name", "Groceries"), items)
        return {"source": source, "cleared": ok}
    if source == "inbox_file":
        return {"source": source, "cleared": clear_inbox(cfg)}
    if source == "google_doc":
        return {"source": source, "cleared": False,
                "reason": "link-shared docs are read-only; the user clears it themselves "
                          "(or switches that list to Notes/Reminders/inbox)"}
    return {"source": source, "cleared": False, "reason": f"unknown source {source!r}"}
