"""Cross-process checkout mutex.

Two interfaces can now reach checkout — the local web UI runs its own in-process
gateway, while the Claude connector hits the launchd `grocery-gateway --http`. Those are
separate processes, so an in-memory lock wouldn't help. `checkout_lock()` takes an
exclusive `flock` on a file under the shared agent home, serializing the place-order
critical section across processes.

This adds mutual exclusion only — it changes no money-safety semantics. A single
interface acquires it uncontended (instant); it blocks only when a second interface tries
to check out at the same moment.

WARNING (self-deadlock): `flock` locks are tied to the open file *description*, so two
separate `open()`s of the same path in ONE process are two descriptions — the second
`LOCK_EX` would block forever waiting on the first. So callers that already hold the lock
(e.g. place_order around approvals.consume) must use the *_locked helpers that assume the
lock is held, never re-enter checkout_lock().
"""

import fcntl
from contextlib import contextmanager

from . import config


@contextmanager
def checkout_lock():
    path = config.checkout_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
