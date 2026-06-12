"""Append-only audit records for every checkout attempt. One JSON file per attempt
in data/orders/; the policy engine reads these back for rolling spend totals."""

import json
import uuid
from datetime import datetime
from pathlib import Path

from . import config


def new_record(kind: str, **fields) -> dict:
    rec = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,  # "placed" | "dry_run" | "blocked" | "pending_approval"
        "placed_at": datetime.now().isoformat(timespec="seconds"),
        **fields,
    }
    path = config.orders_dir() / f"{rec['placed_at'].replace(':', '')}-{rec['kind']}-{rec['id']}.json"
    path.write_text(json.dumps(rec, indent=2))
    return rec


def all_records() -> list[dict]:
    records = []
    for p in sorted(config.orders_dir().glob("*.json")):
        try:
            records.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return records


def placed_orders() -> list[dict]:
    """Only real, completed purchases count toward spend limits."""
    return [r for r in all_records() if r.get("kind") == "placed" and "total" in r]


def screenshots_dir(order_id: str) -> Path:
    d = config.orders_dir() / "screenshots" / order_id
    d.mkdir(parents=True, exist_ok=True)
    return d
