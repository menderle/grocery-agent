"""Calendar awareness via secret ICS feed URLs (GROCERY_ICS_URLS in .env) — works with
Google Calendar's "secret address in iCal format" and iCloud public calendar links.
No OAuth; the URL itself is the credential, which is why it lives in .env.

The agent uses upcoming events to *suggest* groceries ("dinner party Saturday — want
appetizer supplies in this order?"); the suggestion intelligence is the LLM's job, this
module just supplies clean event data."""

import os
import re
import urllib.request
from datetime import date, timedelta


def _unfold(text: str) -> str:
    # RFC 5545: long lines continue with CRLF + single space/tab.
    return re.sub(r"\r?\n[ \t]", "", text)


def parse_ics(text: str) -> list[dict]:
    """Minimal VEVENT parser: start date + summary + optional location.
    Recurring events are reported by their first DTSTART only (no RRULE expansion)."""
    events = []
    for block in _unfold(text).split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        fields = {}
        for line in block.splitlines():
            for key in ("DTSTART", "SUMMARY", "LOCATION", "RRULE"):
                if line.startswith(key):
                    fields[key] = line.partition(":")[2].strip()
        m = re.search(r"(\d{8})", fields.get("DTSTART", ""))
        if not m or "SUMMARY" not in fields:
            continue
        raw = m.group(1)
        events.append({
            "date": f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}",
            "summary": fields["SUMMARY"],
            "location": fields.get("LOCATION") or None,
            "recurring": "RRULE" in fields,
        })
    return events


def upcoming_events(days: int = 7) -> dict:
    urls = [u.strip() for u in os.environ.get("GROCERY_ICS_URLS", "").split(",") if u.strip()]
    if not urls:
        return {
            "available": False,
            "hint": "set GROCERY_ICS_URLS in .env (Google Calendar: Settings → "
                    "'Secret address in iCal format'; iCloud: public calendar link)",
        }
    today, horizon = date.today(), date.today() + timedelta(days=days)
    events, errors = [], []
    for url in urls:
        url = url.replace("webcal://", "https://")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                parsed = parse_ics(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            errors.append(f"{url[:60]}…: {e}")
            continue
        for ev in parsed:
            if today <= date.fromisoformat(ev["date"]) <= horizon:
                events.append(ev)
    events.sort(key=lambda e: e["date"])
    out = {"available": True, "window_days": days, "events": events}
    if errors:
        out["feed_errors"] = errors
    return out
