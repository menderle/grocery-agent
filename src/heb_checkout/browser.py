"""Shared Playwright context matching texas-grocery-mcp's fingerprint exactly
(same UA, launch args, and auth.json storage state) so HEB sees one consistent client."""

import asyncio
import json
import random
import time
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

from . import config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
]


class SessionExpiredError(RuntimeError):
    """The HEB session is signed out / expired — recoverable by re-login, NOT a transient
    checkout failure to retry. Callers map this to a structured 'needs_login' result so the
    user gets a clear re-login prompt in <1s instead of an opaque mid-walk Playwright timeout."""


# Durable HEB login cookies — a valid one of these means "signed in", so no human re-login is
# needed. We deliberately do NOT check the reese84 renewTime here: reese84 is a short (~8-min)
# anti-bot token the parked-Chrome sync refreshes every 180s, so gating needs_login on it would
# wrongly report a perfectly healthy session as signed-out in the gap between syncs (a sync, not
# a re-login, fixes that). A genuinely rejected session still surfaces via the in-walk sign-in
# redirect (_assert_logged_in) and the bounded action timeout — the ground truth.
_SESSION_COOKIES = ("sat", "sst", "DYN_USER_ID")


def session_live() -> bool:
    """True when a DURABLE HEB login is present (a sat/sst/DYN_USER_ID cookie exists and is
    unexpired). Cheap, no-network. Used to fast-fail with 'needs_login' ONLY when a human
    re-login is actually required. Fails OPEN on an unreadable/locked auth file so a transient
    read error never hard-blocks a real order — the in-walk redirect check is the backstop."""
    auth_path = config.auth_state_path()
    try:
        state = json.loads(auth_path.read_text())
    except (OSError, json.JSONDecodeError):
        return auth_path.exists()  # present but unreadable → don't hard-block; let the walk decide
    for c in state.get("cookies", []):
        if c.get("name") in _SESSION_COOKIES and "heb.com" in c.get("domain", ""):
            exp = c.get("expires", -1)
            if exp == -1 or (isinstance(exp, (int, float)) and time.time() < float(exp)):
                return True
    return False


@asynccontextmanager
async def heb_page(headless: bool = True):
    # Pre-flight: never launch the checkout walk on a dead session — fail fast with a typed
    # error the caller turns into a clear 'needs_login' instead of timing out mid-walk.
    if not session_live():
        raise SessionExpiredError(
            "No live HEB session (signed out or token expired). Re-login on the host: make "
            "sure the parked Chrome is logged in (scripts/start_parked_chrome.sh) or run "
            "scripts/sync_parked_session.py."
        )
    auth_path = config.auth_state_path()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=LAUNCH_ARGS)
        context = await browser.new_context(user_agent=USER_AGENT, storage_state=str(auth_path))
        page = await context.new_page()
        # Bound the wait so a missing control on a logged-out/partial page errors in ~15s, not
        # the 30s Playwright default — fast enough to not hang, loose enough not to false-fail a
        # legitimately slow-but-valid control on a poor network. (The real fast-fail for a dead
        # session is the cheap session_live() pre-flight + the _assert_logged_in redirect check.)
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        try:
            yield page
            # Persist any refreshed cookies back for texas-grocery-mcp too. This runs AFTER a
            # committed order on the place() path, so a write-back failure must NEVER propagate
            # (it would unwind past place()'s return → caller restores the approval → re-place
            # → double order). Best-effort only.
            try:
                await context.storage_state(path=str(auth_path))
            except Exception:
                pass
        finally:
            await browser.close()


async def human_pause(low: float = 0.8, high: float = 2.4) -> None:
    """Human-paced delay between actions; keeps automation under bot-detection thresholds."""
    await asyncio.sleep(random.uniform(low, high))
