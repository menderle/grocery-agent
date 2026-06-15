"""Cross-process mutexes.

Two interfaces can reach checkout — the local web UI runs its own in-process gateway,
while the Claude connector hits the launchd `grocery-gateway --http`. Those are separate
processes, so an in-memory lock wouldn't help. We take an exclusive `flock` on a file
under the shared agent home.

This adds mutual exclusion only — it changes no money-safety semantics.

ASYNC SAFETY (important): a blocking `flock` must NOT be acquired directly on an asyncio
event-loop thread and held across `await`s — under contention that freezes the whole loop
(every other request/SSE stream stalls, and a same-loop double-checkout self-deadlocks).
So `place_order` (async) uses `async_checkout_lock()`, which acquires/releases the flock
off-loop via `asyncio.to_thread`; the loop keeps serving while a contender waits.

SELF-DEADLOCK: `flock` is tied to the open file *description*, so two separate `open()`s of
the same path in ONE process are two descriptions — re-acquiring would block forever.
Callers already holding a lock must use the `*_locked` helpers (e.g. approvals.consume_locked)
that assume the lock is held and never re-enter.
"""

import asyncio
import fcntl
from contextlib import asynccontextmanager, contextmanager

from . import config


def _acquire(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, "w")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX)  # blocking; call off-loop in async code
    return fd


def _release(fd):
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


@contextmanager
def checkout_lock():
    """Synchronous exclusive checkout lock. For sync callers (e.g. selftest,
    approvals.consume) only — never on an asyncio loop thread held across awaits."""
    fd = _acquire(config.checkout_lock_path())
    try:
        yield
    finally:
        _release(fd)


@asynccontextmanager
async def async_checkout_lock():
    """Async exclusive checkout lock. Acquires/releases the blocking flock via
    asyncio.to_thread so the event loop keeps running while a contender waits — no
    loop-wide freeze, no same-loop self-deadlock. Used by the async place_order."""
    fd = await asyncio.to_thread(_acquire, config.checkout_lock_path())
    try:
        yield
    finally:
        await asyncio.to_thread(_release, fd)


@contextmanager
def prefs_lock():
    """Synchronous exclusive lock for preferences/staples read-modify-write. These writes
    are fast and the memory tools are sync, so a sync flock is fine (separate lock file so
    it never contends with checkout)."""
    fd = _acquire(config.prefs_lock_path())
    try:
        yield
    finally:
        _release(fd)
