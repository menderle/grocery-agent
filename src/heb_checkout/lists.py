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

def _as_str(s: str) -> str:
    """Escape a Python string for safe embedding inside an AppleScript double-quoted
    literal. Without this, a note title / reminder name / list item containing a quote
    could inject arbitrary AppleScript (it reaches osascript via subprocess)."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _osascript(script: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        # Most likely the macOS automation-permission dialog is waiting for a human
        # click — degrade to unavailable instead of blowing up the whole read.
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def read_apple_notes(note_title: str) -> dict:
    body = _osascript(
        f'tell application "Notes" to get body of note "{_as_str(note_title)}"'
    )
    if body is None:
        return {"available": False,
                "hint": f'no Notes note titled "{note_title}" (or not on macOS)'}
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", body))
    return {"available": True, "items": parse_items(text)}


def mark_apple_notes_handled(note_title: str, items: list[str]) -> bool:
    """Rewrite the note with handled items prefixed by ✓ and a dated footer."""
    body = _osascript(f'tell application "Notes" to get body of note "{_as_str(note_title)}"')
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
    # html.escape does NOT escape backslashes, and new_body is placed inside an
    # AppleScript string literal, so it still needs AppleScript escaping too.
    new_body = "".join(f"<div>{html_lib.escape(line)}</div>" for line in out_lines)
    return _osascript(
        f'tell application "Notes" to set body of note "{_as_str(note_title)}" '
        f'to "{_as_str(new_body)}"'
    ) is not None


def read_apple_reminders(list_name: str) -> dict:
    out = _osascript(
        'set AppleScript\'s text item delimiters to linefeed\n'
        f'tell application "Reminders" to get (name of reminders of list "{_as_str(list_name)}" '
        'whose completed is false) as text'
    )
    if out is None:
        return {"available": False,
                "hint": f'no Reminders list named "{list_name}" (or not on macOS)'}
    return {"available": True, "items": [s for s in out.splitlines() if s.strip()]}


def complete_apple_reminders(list_name: str, items: list[str]) -> bool:
    handled = "{" + ", ".join(f'"{_as_str(i)}"' for i in items) + "}"
    return _osascript(
        f'set handledNames to {handled}\n'
        'tell application "Reminders"\n'
        f'  repeat with r in (reminders of list "{_as_str(list_name)}" whose completed is false)\n'
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


# ---------- iMessage (macOS, off by default — needs Full Disk Access) ----------

def _imessage_seen_path() -> Path:
    return config.agent_home() / "data" / ".imessage-seen.json"


def _imessage_query(cfg: dict) -> list[tuple[int, str]] | None:
    """(rowid, text) for trigger-prefixed messages in the lookback window, or None
    if the DB is unreadable (not macOS / no Full Disk Access)."""
    import sqlite3
    import time
    if platform.system() != "Darwin":
        return None
    db = Path.home() / "Library" / "Messages" / "chat.db"
    prefix = (cfg.get("trigger_prefix") or "grocery:").lower()
    lookback_days = cfg.get("lookback_days", 7)
    apple_epoch_ns = int((time.time() - lookback_days * 86400 - 978307200) * 1e9)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT ROWID, text FROM message WHERE text IS NOT NULL AND date > ?",
            (apple_epoch_ns,),
        ).fetchall()
        con.close()
    except sqlite3.OperationalError:
        return None
    return [(rid, t) for rid, t in rows if t and t.lower().startswith(prefix)]


def read_imessage(cfg: dict) -> dict:
    rows = _imessage_query(cfg)
    if rows is None:
        return {"available": False,
                "hint": "iMessage source needs macOS and Full Disk Access for the "
                        "process running the agent (System Settings → Privacy)"}
    seen = set()
    if _imessage_seen_path().exists():
        seen = set(yaml.safe_load(_imessage_seen_path().read_text()) or [])
    prefix_len = len(cfg.get("trigger_prefix") or "grocery:")
    items = []
    for rowid, text in rows:
        if rowid in seen:
            continue
        items.extend(parse_items(text[prefix_len:].replace(",", "\n")))
    return {"available": True, "items": items}


def clear_imessage(cfg: dict) -> bool:
    """Mark all currently-matching trigger messages as processed (the DB itself is
    never written)."""
    rows = _imessage_query(cfg)
    if rows is None:
        return False
    seen = set()
    if _imessage_seen_path().exists():
        seen = set(yaml.safe_load(_imessage_seen_path().read_text()) or [])
    seen.update(rid for rid, _ in rows)
    _imessage_seen_path().write_text(yaml.safe_dump(sorted(seen)))
    return True


# ---------- Todoist (REST v2, token in .env) ----------

def _http_json(url: str, headers: dict, method: str = "GET", payload: dict | None = None):
    import json
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body.strip() else {}


def _todoist_tasks(cfg: dict) -> list[dict] | None:
    import os
    token = os.environ.get("TODOIST_API_TOKEN", "")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    project_name = (cfg.get("project") or "Groceries").lower()
    projects = _http_json("https://api.todoist.com/rest/v2/projects", headers)
    project = next((p for p in projects if p["name"].lower() == project_name), None)
    if project is None:
        return []
    return _http_json(
        f"https://api.todoist.com/rest/v2/tasks?project_id={project['id']}", headers)


def read_todoist(cfg: dict) -> dict:
    try:
        tasks = _todoist_tasks(cfg)
    except Exception as e:
        return {"available": False, "hint": f"Todoist API error: {e}"}
    if tasks is None:
        return {"available": False, "hint": "set TODOIST_API_TOKEN in .env"}
    return {"available": True, "items": [t["content"] for t in tasks]}


def clear_todoist(cfg: dict, items: list[str]) -> bool:
    import os
    try:
        tasks = _todoist_tasks(cfg)
    except Exception:
        return False
    if not tasks:
        return tasks is not None
    handled = {i.lower() for i in items}
    headers = {"Authorization": f"Bearer {os.environ['TODOIST_API_TOKEN']}"}
    for t in tasks:
        if t["content"].lower() in handled:
            _http_json(f"https://api.todoist.com/rest/v2/tasks/{t['id']}/close",
                       headers, method="POST", payload={})
    return True


# ---------- Notion (to-do blocks on a page, token in .env) ----------

NOTION_HEADERS_BASE = {"Notion-Version": "2022-06-28"}


def _notion_todos() -> list[dict] | None:
    import os
    token = os.environ.get("NOTION_API_TOKEN", "")
    page_id = os.environ.get("NOTION_PAGE_ID", "")
    if not token or not page_id:
        return None
    headers = {"Authorization": f"Bearer {token}", **NOTION_HEADERS_BASE}
    data = _http_json(
        f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100", headers)
    todos = []
    for block in data.get("results", []):
        if block.get("type") == "to_do" and not block["to_do"].get("checked"):
            text = "".join(rt.get("plain_text", "") for rt in block["to_do"].get("rich_text", []))
            if text.strip():
                todos.append({"id": block["id"], "text": text.strip()})
    return todos


def read_notion(cfg: dict) -> dict:
    try:
        todos = _notion_todos()
    except Exception as e:
        return {"available": False, "hint": f"Notion API error: {e} (is the page shared with the integration?)"}
    if todos is None:
        return {"available": False, "hint": "set NOTION_API_TOKEN and NOTION_PAGE_ID in .env"}
    return {"available": True, "items": [t["text"] for t in todos]}


def clear_notion(items: list[str]) -> bool:
    import os
    try:
        todos = _notion_todos()
    except Exception:
        return False
    if todos is None:
        return False
    handled = {i.lower() for i in items}
    headers = {"Authorization": f"Bearer {os.environ['NOTION_API_TOKEN']}", **NOTION_HEADERS_BASE}
    for t in todos:
        if t["text"].lower() in handled:
            _http_json(f"https://api.notion.com/v1/blocks/{t['id']}", headers,
                       method="PATCH", payload={"to_do": {"checked": True}})
    return True


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
    im_cfg = cfg.get("imessage") or {}
    if im_cfg.get("enabled", False):
        sources["imessage"] = read_imessage(im_cfg)
    import os
    if os.environ.get("TODOIST_API_TOKEN"):
        sources["todoist"] = read_todoist(cfg.get("todoist") or {})
    if os.environ.get("NOTION_API_TOKEN") and (cfg.get("notion") or {}).get("enabled", True):
        sources["notion"] = read_notion(cfg.get("notion") or {})
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
    if source == "imessage":
        return {"source": source, "cleared": clear_imessage(cfg.get("imessage") or {})}
    if source == "todoist":
        return {"source": source, "cleared": clear_todoist(cfg.get("todoist") or {}, items)}
    if source == "notion":
        return {"source": source, "cleared": clear_notion(items)}
    if source == "google_doc":
        return {"source": source, "cleared": False,
                "reason": "link-shared docs are read-only; the user clears it themselves "
                          "(or switches that list to Notes/Reminders/inbox)"}
    return {"source": source, "cleared": False, "reason": f"unknown source {source!r}"}
