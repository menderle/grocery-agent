"""Receipt-driven replenishment: learn each item's purchase cycle from the audit log
(placed orders carry their cart items) and flag what's probably running low.

Needs >=2 purchases of an item before predicting; until then the staples list is the
fallback and this returns building_history for visibility."""

import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from . import audit


def _norm(name: str) -> str:
    """Normalize item names so 'H-E-B Whole Milk, 1 gal' matches 'whole milk gallon'."""
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    drop = {"heb", "h", "e", "b", "the", "a", "of", "ct", "oz", "lb", "gal", "pack", "count"}
    words = [w for w in s.split() if w and not w.isdigit() and w not in drop]
    return " ".join(words)


def purchase_history() -> dict[str, list[date]]:
    history: dict[str, list[date]] = defaultdict(list)
    for rec in audit.placed_orders():
        day = date.fromisoformat(rec["placed_at"][:10])
        for item in rec.get("items") or []:
            name = item.get("name") if isinstance(item, dict) else str(item)
            if name:
                history[_norm(name)].append(day)
    return {k: sorted(v) for k, v in history.items()}


def suggest(horizon_days: int = 7, today: date | None = None) -> dict:
    today = today or date.today()
    due, building = [], []
    for name, days_bought in purchase_history().items():
        if len(days_bought) < 2:
            building.append(name)
            continue
        intervals = [(b - a).days for a, b in zip(days_bought, days_bought[1:])]
        cycle = max(1, round(median(intervals)))
        last = days_bought[-1]
        due_date = last + timedelta(days=cycle)
        if due_date <= today + timedelta(days=horizon_days):
            due.append({
                "item": name,
                "last_bought": last.isoformat(),
                "cycle_days": cycle,
                "due": due_date.isoformat(),
                "overdue_days": max(0, (today - due_date).days),
                "times_bought": len(days_bought),
            })
    due.sort(key=lambda d: d["due"])
    return {
        "horizon_days": horizon_days,
        "due_or_due_soon": due,
        "building_history": sorted(building),
        "note": "predictions need >=2 purchases per item; until then rely on staples.json",
    }
