"""Append-only audit records for every checkout attempt. One JSON file per attempt
in data/orders/; the policy engine reads these back for rolling spend totals."""

import json
import os
import tempfile
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
    # Atomic write so a crash can't leave a half-written record that all_records() then
    # silently drops (which would undercount rolling spend).
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rec, indent=2))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
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
    """Only real, completed purchases count toward spend limits. Defensive: a record with
    a non-numeric total or an unparseable timestamp is skipped rather than crashing every
    spend calculation downstream (get_policy, policy.evaluate, /api/status)."""
    out = []
    for r in all_records():
        if r.get("kind") != "placed":
            continue
        if not isinstance(r.get("total"), (int, float)):
            continue
        try:
            datetime.fromisoformat(r["placed_at"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(r)
    return out


def screenshots_dir(order_id: str) -> Path:
    d = config.orders_dir() / "screenshots" / order_id
    d.mkdir(parents=True, exist_ok=True)
    return d
