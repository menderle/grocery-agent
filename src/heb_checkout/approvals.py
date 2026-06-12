"""Pending-approval store. An order that needs human sign-off is parked here with an
expiry; approving releases exactly that order (same total, fulfillment, slot)."""

import json
import uuid
from datetime import datetime, timedelta

from . import config


def _load() -> dict:
    path = config.approvals_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save(data: dict) -> None:
    config.approvals_path().write_text(json.dumps(data, indent=2))


def create(order_total: float, fulfillment: str, slot_text: str | None, expiry_hours: float) -> dict:
    data = _load()
    approval = {
        "id": uuid.uuid4().hex[:8],
        "order_total": order_total,
        "fulfillment": fulfillment,
        "slot_text": slot_text,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "expires_at": (datetime.now() + timedelta(hours=expiry_hours)).isoformat(timespec="seconds"),
    }
    data[approval["id"]] = approval
    _save(data)
    return approval


def consume(approval_id: str) -> dict:
    """Pop an approval; raises if missing or expired."""
    data = _load()
    approval = data.pop(approval_id, None)
    _save(data)
    if approval is None:
        raise ValueError(f"no pending approval {approval_id!r}")
    if datetime.fromisoformat(approval["expires_at"]) < datetime.now():
        raise ValueError(f"approval {approval_id} expired at {approval['expires_at']}; ask for a fresh cart summary")
    return approval


def restore(approval: dict) -> None:
    """Put a consumed approval back (checkout failed for technical reasons after
    consume — the human's yes shouldn't be burned by a browser error)."""
    data = _load()
    data[approval["id"]] = approval
    _save(data)


def pending() -> list[dict]:
    now = datetime.now()
    return [a for a in _load().values() if datetime.fromisoformat(a["expires_at"]) >= now]
