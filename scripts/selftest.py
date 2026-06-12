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

from heb_checkout import approvals, audit, policy  # noqa: E402

noon = datetime(2026, 6, 12, 12, 0)

d = policy.evaluate(80.0, now=noon)
assert d.action == "needs_approval", d
d = policy.evaluate(250.0, now=noon, approved=True)
assert d.action == "blocked" and "per_order" in d.reason, d
d = policy.evaluate(50.0, now=datetime(2026, 6, 12, 23, 30))
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

shutil.rmtree(tmp)
print("selftest: all checks passed")
