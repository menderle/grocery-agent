"""Policy engine. place_order calls evaluate() before any money moves — the decision
is computed from config/policy.yaml plus the audit log, never from model output."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import yaml

from . import audit, config

SETTABLE_FIELDS = {
    "mode": {"approve", "auto_under_threshold", "full_auto"},
    "auto_threshold": None,
    "fulfillment": {"pickup", "delivery", "ask"},
}
SETTABLE_LIMITS = {"per_order", "weekly", "monthly"}


@dataclass
class Decision:
    action: str  # "allow" | "needs_approval" | "blocked"
    reason: str


def load() -> dict:
    with open(config.policy_path()) as f:
        return yaml.safe_load(f)


def save(policy: dict) -> None:
    with open(config.policy_path(), "w") as f:
        yaml.safe_dump(policy, f, sort_keys=False)


def _in_quiet_hours(policy: dict, now: datetime) -> bool:
    window = policy.get("quiet_hours")
    if not window:
        return False
    start, end = (datetime.strptime(t, "%H:%M").time() for t in window)
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window crosses midnight


def evaluate(order_total: float, now: datetime | None = None, approved: bool = False) -> Decision:
    """Decide whether an order of `order_total` dollars may be placed right now.
    `approved=True` means a human already confirmed this specific order."""
    policy = load()
    now = now or datetime.now()
    limits = policy.get("spend_limits", {})
    history = audit.placed_orders()

    def total_since(days: int) -> float:
        cutoff = now - timedelta(days=days)
        return sum(o["total"] for o in history if datetime.fromisoformat(o["placed_at"]) >= cutoff)

    # Hard blocks apply in every mode — approval cannot override them.
    per_order = limits.get("per_order")
    if per_order is not None and order_total > per_order:
        return Decision("blocked", f"order ${order_total:.2f} exceeds per_order limit ${per_order:.2f}")
    weekly = limits.get("weekly")
    if weekly is not None and total_since(7) + order_total > weekly:
        return Decision(
            "blocked",
            f"order would bring rolling 7-day total to ${total_since(7) + order_total:.2f}, over weekly limit ${weekly:.2f}",
        )
    monthly = limits.get("monthly")
    if monthly is not None and total_since(30) + order_total > monthly:
        return Decision(
            "blocked",
            f"order would bring rolling 30-day total to ${total_since(30) + order_total:.2f}, over monthly limit ${monthly:.2f}",
        )
    max_per_day = policy.get("max_orders_per_day")
    if max_per_day is not None:
        today = [o for o in history if datetime.fromisoformat(o["placed_at"]).date() == now.date()]
        if len(today) >= max_per_day:
            return Decision("blocked", f"already placed {len(today)} order(s) today (max_orders_per_day={max_per_day})")
    if _in_quiet_hours(policy, now):
        return Decision("blocked", f"inside quiet hours {policy['quiet_hours']}")

    if approved:
        return Decision("allow", "human-approved order within limits")

    mode = policy.get("mode", "approve")
    if mode == "full_auto":
        return Decision("allow", "full_auto mode, within limits")
    if mode == "auto_under_threshold":
        threshold = policy.get("auto_threshold", 0)
        if order_total <= threshold:
            return Decision("allow", f"${order_total:.2f} ≤ auto_threshold ${threshold:.2f}")
        return Decision("needs_approval", f"${order_total:.2f} exceeds auto_threshold ${threshold:.2f}")
    return Decision("needs_approval", "approve mode: every order needs confirmation")


def update(field: str, value) -> dict:
    """Update a settable policy field or spend limit (e.g. 'mode', 'spend_limits.weekly')."""
    policy = load()
    if field.startswith("spend_limits."):
        key = field.split(".", 1)[1]
        if key not in SETTABLE_LIMITS:
            raise ValueError(f"unknown spend limit {key!r}; settable: {sorted(SETTABLE_LIMITS)}")
        policy.setdefault("spend_limits", {})[key] = float(value)
    elif field in SETTABLE_FIELDS:
        allowed = SETTABLE_FIELDS[field]
        if allowed and value not in allowed:
            raise ValueError(f"{field} must be one of {sorted(allowed)}")
        policy[field] = float(value) if field == "auto_threshold" else value
    else:
        raise ValueError(f"field {field!r} is not settable via the agent; edit policy.yaml directly")
    save(policy)
    return policy
