"""Pending-approval store. An order that needs human sign-off is parked here with an
expiry; approving releases exactly that order (same total, fulfillment, slot)."""

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta

from . import config
from .locking import checkout_lock


def _load() -> dict:
    path = config.approvals_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    """Atomic, self-sufficient write (temp + os.replace) so a crash mid-write can't
    corrupt the pending-approval ledger, and the data/ dir is created if missing."""
    path = config.approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def create(order_total: float, fulfillment: str, slot_text: str | None, expiry_hours: float,
           items: list | None = None) -> dict:
    data = _load()
    approval = {
        "id": uuid.uuid4().hex[:8],
        "order_total": order_total,
        "fulfillment": fulfillment,
        "slot_text": slot_text,
        "items": items or [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "expires_at": (datetime.now() + timedelta(hours=expiry_hours)).isoformat(timespec="seconds"),
    }
    data[approval["id"]] = approval
    _save(data)
    return approval


def consume_locked(approval_id: str) -> dict:
    """Pop an approval, ASSUMING the checkout lock is already held by the caller.
    place_order holds the lock around its whole critical section, so it must use this
    (re-taking checkout_lock from the same process would self-deadlock — see locking.py).
    Raises if missing or expired."""
    data = _load()
    approval = data.pop(approval_id, None)
    _save(data)
    if approval is None:
        raise ValueError(f"no pending approval {approval_id!r}")
    if datetime.fromisoformat(approval["expires_at"]) < datetime.now():
        raise ValueError(f"approval {approval_id} expired at {approval['expires_at']}; ask for a fresh cart summary")
    return approval


def consume(approval_id: str) -> dict:
    """Pop an approval atomically across processes (web UI + Claude connector); raises if
    missing or expired. Takes the checkout lock so the same approval can't be consumed
    twice by two simultaneous interfaces."""
    with checkout_lock():
        return consume_locked(approval_id)


def restore(approval: dict) -> None:
    """Put a consumed approval back (checkout failed for technical reasons after
    consume — the human's yes shouldn't be burned by a browser error)."""
    data = _load()
    data[approval["id"]] = approval
    _save(data)


def pending() -> list[dict]:
    now = datetime.now()
    return [a for a in _load().values() if datetime.fromisoformat(a["expires_at"]) >= now]
