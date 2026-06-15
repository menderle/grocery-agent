"""Self-test for the policy engine, audit log, and approval store. No network, no
browser — safe to run anywhere: .venv/bin/python scripts/selftest.py"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime

tmp = tempfile.mkdtemp()
os.environ["GROCERY_AGENT_HOME"] = tmp
pathlib.Path(tmp, "config").mkdir()
pathlib.Path(tmp, "data").mkdir()
repo = pathlib.Path(__file__).resolve().parents[1]
shutil.copy(repo / "config" / "policy.yaml", tmp + "/config/policy.yaml")
# The decision assertions below assume the default 'approve' mode, but the live repo
# policy.yaml may be full_auto (the app writes the user's autonomy choice straight there),
# so pin a known mode — the policy-engine test must not depend on the user's current setting.
import yaml as _yaml0  # noqa: E402
_pf0 = pathlib.Path(tmp, "config", "policy.yaml")
_pd0 = _yaml0.safe_load(_pf0.read_text()); _pd0["mode"] = "approve"
_pf0.write_text(_yaml0.safe_dump(_pd0))

from heb_checkout import approvals, audit, policy  # noqa: E402

# Use TODAY's real date: audit.new_record() stamps with the real clock, so the
# max_orders_per_day "orders today" check only lines up if the test evaluates as-of today.
_t = datetime.now()
noon = _t.replace(hour=12, minute=0, second=0, microsecond=0)

d = policy.evaluate(80.0, now=noon)
assert d.action == "needs_approval", d
d = policy.evaluate(250.0, now=noon, approved=True)
assert d.action == "blocked" and "per_order" in d.reason, d
d = policy.evaluate(50.0, now=noon.replace(hour=23, minute=30))
assert d.action == "blocked" and "quiet" in d.reason, d
d = policy.evaluate(80.0, now=noon, approved=True)
assert d.action == "allow", d

policy.update("mode", "full_auto")
assert policy.evaluate(80.0, now=noon).action == "allow"
audit.new_record("placed", total=380.0)
d = policy.evaluate(80.0, now=noon)
assert d.action == "blocked" and "weekly" in d.reason, d

policy.update("spend_limits.weekly", 1000)
d = policy.evaluate(80.0, now=noon)
assert d.action == "blocked" and "max_orders_per_day" in d.reason, d

for f in pathlib.Path(tmp, "data/orders").glob("*.json"):
    f.unlink()
policy.update("mode", "auto_under_threshold")
assert policy.evaluate(100.0, now=noon).action == "allow"
assert policy.evaluate(180.0, now=noon).action == "needs_approval"

a = approvals.create(120.0, "pickup", "Thu 6-7pm", expiry_hours=4)
assert approvals.pending()
got = approvals.consume(a["id"])
assert got["order_total"] == 120.0
approvals.restore(got)  # technical-failure path puts the approval back
assert approvals.consume(a["id"])["order_total"] == 120.0
for bad in (lambda: approvals.consume(a["id"]),
            lambda: policy.update("mode", "yolo"),
            lambda: policy.update("heb_graphql_url", "http://evil")):
    try:
        bad()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

from heb_checkout.cards import last4, luhn_ok  # noqa: E402
from heb_checkout.checkout_driver import parse_dollars  # noqa: E402

assert luhn_ok("5555555555554444")          # valid test Mastercard
assert not luhn_ok("5555555555554443")      # bad checksum
assert not luhn_ok("12345")                 # too short
assert last4("5555 5555 5555 4444") == "4444"

assert parse_dollars("Total: $123.45") == 123.45
assert parse_dollars("$1,234.56 estimated") == 1234.56
assert parse_dollars("$87") == 87.0
assert parse_dollars("no money here") is None
assert parse_dollars(None) is None

from heb_checkout import lists  # noqa: E402

parsed = lists.parse_items(
    "Groceries:\n- milk\n* eggs\n[ ] bread\n[x] butter\n☐ salsa\n✓ already ordered\n\nlimes  "
)
assert parsed == ["milk", "eggs", "bread", "butter", "salsa", "limes"], parsed

assert lists._gdoc_export_url("https://docs.google.com/document/d/abc-123_X/edit?usp=sharing") \
    == "https://docs.google.com/document/d/abc-123_X/export?format=txt"
assert lists._gdoc_export_url("https://docs.google.com/spreadsheets/d/s99/edit#gid=0") \
    == "https://docs.google.com/spreadsheets/d/s99/export?format=csv"
assert lists._gdoc_export_url("https://example.com/nope") is None

# inbox round-trip inside the temp GROCERY_AGENT_HOME
(pathlib.Path(tmp) / "config" / "lists.yaml").write_text(
    "apple_notes:\n  enabled: false\napple_reminders:\n  enabled: false\n"
    "inbox_file:\n  path: data/inbox.md\n"
)
assert lists.append_inbox("- avocados\ntortillas\n") == 2
assert lists.read_inbox(lists._cfg())["items"] == ["avocados", "tortillas"]
assert lists.clear(source="inbox_file", items=["avocados"])["cleared"]
assert lists.read_inbox(lists._cfg())["items"] == []
assert lists.clear(source="google_doc", items=[])["cleared"] is False  # read-only

# --- calendar: ICS parsing (no network) ---
from heb_checkout.calendar_events import parse_ics  # noqa: E402

ICS = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260615\r\n"
    "SUMMARY:Dinner party with the\r\n  Garcias\r\nLOCATION:Home\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;TZID=America/Chicago:20260620T180000\r\n"
    "SUMMARY:Weekly standup\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
events = parse_ics(ICS)
assert events[0] == {"date": "2026-06-15", "summary": "Dinner party with the Garcias",
                     "location": "Home", "recurring": False}, events[0]
assert events[1]["date"] == "2026-06-20" and events[1]["recurring"], events[1]

# --- replenishment: cycle math from fabricated placed orders ---
from heb_checkout import replenishment  # noqa: E402

for day in ("2026-05-01", "2026-05-08", "2026-05-16", "2026-05-23"):
    rec = audit.new_record("placed", total=50.0, items=[{"name": "H-E-B Whole Milk, 1 gal"}])
    # rewrite the timestamp the record was stamped with (locate by unique id)
    path = next(pathlib.Path(tmp, "data/orders").glob(f"*{rec['id']}*.json"))
    data = json.loads(path.read_text())
    data["placed_at"] = day + "T10:00:00"
    path.write_text(json.dumps(data))
audit.new_record("placed", total=10.0, items=[{"name": "birthday candles"}])

sug = replenishment.suggest(horizon_days=7, today=__import__("datetime").date(2026, 5, 30))
due = {d["item"]: d for d in sug["due_or_due_soon"]}
assert "whole milk gal" in " ".join(due) or "whole milk" in " ".join(due), due
milk = next(iter(due.values()))
assert milk["cycle_days"] == 7 and milk["times_bought"] == 4, milk
assert "birthday candles" in sug["building_history"], sug["building_history"]

# --- preferences / product memory round-trip (incl. atomic save) ---
from heb_checkout import preferences  # noqa: E402

preferences.remember("my usual water", display_name="H-E-B 1877 Mineral Water 12 pk",
                     product_id="123", sku_id="456")
hit = preferences.resolve("buy a 12-pack of water")          # filler/quantity-word match
assert hit and hit["product_id"] == "123", hit
assert preferences.resolve("water")["sku_id"] == "456"
preferences.remember("water", brand="H-E-B")                 # merge: keeps the saved sku
assert preferences.resolve("water")["sku_id"] == "456" and preferences.resolve("water")["brand"] == "H-E-B"
assert preferences.resolve("rose water") is None             # exact-match: no generic false positive
assert preferences.resolve("sparkling water") is None
preferences.remember("water", overwrite=False, sku_id="999")  # auto-learn must NOT clobber curated
assert preferences.resolve("water")["sku_id"] == "456"
assert preferences.forget("water") is True
assert preferences.resolve("water") is None
preferences.add_staple("oat milk", quantity=2)
assert any(s["query"] == "oat milk" for s in preferences.staples())
preferences.remove_staple("oat milk")
assert not any(s["query"] == "oat milk" for s in preferences.staples())

# --- checkout lock: consume() takes it; round-trips and doesn't self-deadlock ---
from heb_checkout.locking import checkout_lock  # noqa: E402

b = approvals.create(60.0, "pickup", None, expiry_hours=4)
with checkout_lock():                                          # held → must use consume_locked
    assert approvals.consume_locked(b["id"])["order_total"] == 60.0
with checkout_lock():
    pass                                                       # re-acquire after release: no deadlock

# --- checkout path smoke: _cart_fingerprint + place_order allow-branch with a stubbed
#     browser (this would have caught the missing `import json` in server.py). No network. ---
import asyncio as _aio  # noqa: E402
import yaml as _yaml  # noqa: E402
from heb_checkout import server, checkout_driver as _cd  # noqa: E402

_fp = server._cart_fingerprint(12.5, [{"name": "Milk", "quantity": 2}])
assert isinstance(_fp, str) and len(_fp) == 16, _fp
assert server._cart_fingerprint(12.5, [{"name": "Milk", "quantity": 2}]) == _fp  # stable

# permissive temp policy so the allow-branch is reached regardless of wall-clock/caps
_pp = pathlib.Path(tmp, "config", "policy.yaml")
_pol = _yaml.safe_load(_pp.read_text())
_pol["mode"] = "full_auto"; _pol.pop("quiet_hours", None)
_pol["max_orders_per_day"] = 999
_pol.setdefault("spend_limits", {}).update({"per_order": 100000, "weekly": 100000, "monthly": 100000})
_pp.write_text(_yaml.safe_dump(_pol))
for _f in pathlib.Path(tmp, "data/orders").glob("*.json"):
    _f.unlink()


async def _fake_place(fulfillment, slot_text, order_id, dry_run, max_total):
    return {"status": "placed", "estimated_total": 7.5, "confirmation": "TEST"}


_cd.place = _fake_place
server.session_live = lambda: True  # checkout pre-flight: don't depend on a live HEB session here
_res = _aio.run(server.place_order(7.5, items=[{"name": "Milk", "quantity": 2}]))
assert _res.get("status") == "placed", _res
assert any(r.get("kind") == "placed" and r.get("fingerprint") for r in audit.all_records()), "placed record missing fingerprint"

# session pre-flight: a signed-out session returns needs_login and never places/charges
server.session_live = lambda: False
_res_nl = _aio.run(server.place_order(9.0, items=[{"name": "Eggs", "quantity": 1}]))
assert _res_nl.get("status") == "needs_login", _res_nl
server.session_live = lambda: True  # restore for anything downstream

# --- new sources gate correctly when unconfigured ---
result = lists.read_all()
assert "todoist" not in result["sources"] and "notion" not in result["sources"]
assert lists.clear("todoist", ["x"])["cleared"] is False  # no token -> graceful

# --- OAuth OwnerOnly middleware: the email allowlist actually gates ---
import asyncio  # noqa: E402
from unittest.mock import patch  # noqa: E402
from heb_checkout import auth  # noqa: E402

mw = auth.OwnerOnly({"owner@example.com"})


async def _gate(claims):
    async def nxt(ctx):
        return "ALLOWED"
    tok = type("T", (), {"claims": claims})() if claims is not None else None
    with patch.object(auth, "get_access_token", lambda: tok):
        try:
            return await mw.on_request(None, nxt)
        except PermissionError:
            return "BLOCKED"

assert asyncio.run(_gate({"email": "owner@example.com", "email_verified": True})) == "ALLOWED"
assert asyncio.run(_gate({"email": "OWNER@EXAMPLE.COM", "email_verified": True})) == "ALLOWED"  # case-insensitive
assert asyncio.run(_gate({"email": "attacker@example.com", "email_verified": True})) == "BLOCKED"
assert asyncio.run(_gate({"email": "owner@example.com", "email_verified": False})) == "BLOCKED"
assert asyncio.run(_gate({})) == "BLOCKED"
assert asyncio.run(_gate(None)) == "ALLOWED"  # public discovery/health hop

shutil.rmtree(tmp)
print("selftest: all checks passed")
