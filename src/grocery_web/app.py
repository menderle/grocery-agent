"""Starlette app + `grocery-web` entry point.

Routes: a single-page chat UI, SSE chat + approve, and a status endpoint. The chat brain
is grocery_web.agent (a Claude tool-use loop over the in-process gateway). All money-safety
state is shared with the Claude connector via disk + the HEB account.
"""

import asyncio
import hmac
import json
import os
import re
import sys
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import agent, config

STATIC = Path(__file__).parent / "static"
NOSTORE = {"cache-control": "no-store"}  # page + API must never be cached (status goes stale)


# ---------- conversation store (per-restart durable; chat threads on disk) ----------

# One asyncio.Lock per conversation id so two concurrent requests for the same thread
# (two tabs, or a chat turn racing the Approve button) can't lost-update the history via
# read-modify-write. Single-process uvicorn, so an in-process dict is sufficient.
_cid_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _safe_cid(cid: str) -> str:
    cid = re.sub(r"[^a-zA-Z0-9_-]", "", cid or "")
    return (cid or uuid.uuid4().hex[:12])[:64]


def _prune_convos(max_age_days: int = 30) -> None:
    """Best-effort retention sweep so transcripts don't accumulate forever."""
    import time
    cutoff = time.time() - max_age_days * 86400
    try:
        for p in config.conversations_dir().glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


_INTERRUPTED = "[interrupted — no result was recorded]"


def _synth_result(tool_use_id: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": _INTERRUPTED, "is_error": True}


def _repair_tool_use_pairs(messages: list) -> list:
    """Keep a persisted thread replayable by the Anthropic Messages API, which rejects BOTH
    (a) an assistant `tool_use` block with no answering `tool_result` in the immediately
    following user message, and (b) a `tool_result` block with no preceding `tool_use`. A turn
    interrupted between the assistant tool_use and its tool_results (stream abort, tool error,
    process restart mid-turn) produces (a); a legacy/hand-edited thread can produce (b). We
    synthesize error tool_results for missing ids and drop orphan tool_results. Idempotent: a
    well-formed thread passes through unchanged. (Adjacent same-role user messages — e.g. a
    synthetic tool_result message right before a user-text message — are fine; the API
    coalesces them.)"""
    out: list = []
    i, n = 0, len(messages)
    while i < n:
        msg = messages[i]
        content = msg.get("content") if isinstance(msg, dict) else None
        ids = ([b["id"] for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")]
               if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(content, list)
               else [])
        if ids:
            out.append(msg)
            nxt = messages[i + 1] if i + 1 < n else None
            nxt_content = nxt.get("content") if isinstance(nxt, dict) else None
            nxt_is_results = (isinstance(nxt, dict) and nxt.get("role") == "user"
                              and isinstance(nxt_content, list)
                              and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                      for b in nxt_content))
            if nxt_is_results:
                # Keep non-tool_result content + tool_results that answer THIS message's ids
                # (drop orphans), then synthesize for any id still unanswered.
                kept = [b for b in nxt_content
                        if not (isinstance(b, dict) and b.get("type") == "tool_result")
                        or b.get("tool_use_id") in ids]
                present = {b.get("tool_use_id") for b in kept
                           if isinstance(b, dict) and b.get("type") == "tool_result"}
                kept += [_synth_result(t) for t in ids if t not in present]
                out.append({**nxt, "content": kept})
                i += 2
                continue
            # No answering message follows — insert one covering every id.
            out.append({"role": "user", "content": [_synth_result(t) for t in ids]})
            i += 1
            continue
        # A user message bearing tool_result blocks that is NOT the answer to a preceding
        # tool_use (an immediate answer would have been consumed above) → orphans; drop them.
        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(content, list) \
                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            kept = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]
            if kept:  # keep any non-tool_result content; if it was all orphans, drop the message
                out.append({**msg, "content": kept})
            i += 1
            continue
        out.append(msg)
        i += 1
    return out


def _load_convo(cid: str) -> dict:
    path = config.conversations_dir() / f"{cid}.json"
    if path.exists():
        try:
            convo = json.loads(path.read_text())
            convo["messages"] = _repair_tool_use_pairs(convo.get("messages") or [])
            return convo
        except (json.JSONDecodeError, OSError):
            pass
    return {"messages": []}


def _save_convo(cid: str, convo: dict) -> None:
    """Atomic write so a crash / disconnect mid-write can't truncate a thread (which
    _load_convo would then silently discard). Repairs tool_use/tool_result pairing first so an
    interrupted turn never persists a thread the Anthropic API will reject on the next request."""
    convo = {**convo, "messages": _repair_tool_use_pairs(convo.get("messages") or [])}
    path = config.conversations_dir() / f"{cid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(convo, indent=2, default=str))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


async def _read_json(request):
    """Parse a JSON object body or raise ValueError (caller returns 400)."""
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return body


# ---------- routes ----------

async def index(request):
    return FileResponse(STATIC / "index.html", headers=NOSTORE)


async def api_config(request):
    return JSONResponse({
        "models": config.model_choices(),
        "default_model": config.default_alias(),
        "auth_required": bool(config.web_auth_token()),
    }, headers=NOSTORE)


async def api_status(request):
    from heb_checkout import audit, config as core, policy
    hist = audit.placed_orders()
    cutoff = datetime.now() - timedelta(days=7)
    spent7 = round(sum(o["total"] for o in hist
                       if datetime.fromisoformat(o["placed_at"]) >= cutoff), 2)
    try:
        mode = policy.load().get("mode")
    except Exception:
        mode = None
    try:
        from heb_checkout.gateway import _store
        sid, sname = _store()
        store = f"{sname} · {sid}"
    except Exception:
        store = None
    from heb_checkout.browser import session_live
    return JSONResponse({
        "dry_run": core.dry_run_default(),
        "heb_session_present": session_live(),  # durable HEB login present, not just the auth file existing
        "policy_mode": mode,
        "spent_last_7_days": spent7,
        "store": store,
    }, headers=NOSTORE)


async def api_settings(request):
    """Human-initiated settings change (autonomy mode). NOT reachable by the agent — the LLM
    tool denylist still blocks set_policy; this is an explicit, token-gated UI action."""
    try:
        body = await _read_json(request)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400, headers=NOSTORE)
    mode = body.get("mode")
    if mode not in ("approve", "auto_under_threshold", "full_auto"):
        return JSONResponse({"error": "invalid mode"}, status_code=400, headers=NOSTORE)
    from heb_checkout import policy
    try:
        pol = policy.update("mode", mode)
    except Exception:
        import traceback
        traceback.print_exc()  # detail to server log; generic message to client
        return JSONResponse({"error": "could not update settings"}, status_code=500, headers=NOSTORE)
    return JSONResponse({"mode": pol.get("mode")}, headers=NOSTORE)


async def health(request):
    from heb_checkout import config as core
    auth = core.auth_state_path()
    return JSONResponse({"ok": auth.exists(), "dry_run_mode": core.dry_run_default()}, headers=NOSTORE)


def _sse(gen):
    async def wrapped():
        try:
            async for ev in gen:
                yield {"event": ev["event"], "data": json.dumps(ev["data"], default=str)}
        except Exception as e:  # surface backend errors to the UI instead of a dead stream
            import traceback
            traceback.print_exc()  # full detail to the server log; concise message to client
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
    return EventSourceResponse(wrapped())


async def api_chat(request):
    try:
        body = await _read_json(request)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    cid = _safe_cid(body.get("conversation_id"))
    model = config.resolve_model(body.get("model"))

    async def gen():
        # Serialize load→run→save per conversation so concurrent requests can't lose history.
        async with _cid_locks[cid]:
            convo = _load_convo(cid)
            convo["messages"].append({"role": "user", "content": message})
            yield {"event": "meta", "data": {"conversation_id": cid, "model": model}}
            try:
                async for ev in agent.run_chat(convo["messages"], model):
                    yield ev
            finally:
                _save_convo(cid, convo)
    return _sse(gen())


async def api_approve(request):
    try:
        body = await _read_json(request)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    approval_id = (body.get("approval_id") or "").strip()
    if not approval_id:
        return JSONResponse({"error": "approval_id required"}, status_code=400)
    cid = _safe_cid(body.get("conversation_id"))
    model = config.resolve_model(body.get("model"))

    async def gen():
        async with _cid_locks[cid]:
            convo = _load_convo(cid)
            yield {"event": "meta", "data": {"conversation_id": cid, "model": model}}
            try:
                async for ev in agent.approve_order(convo["messages"], approval_id, model):
                    yield ev
            finally:
                _save_convo(cid, convo)
    return _sse(gen())


# ---------- token auth (pure ASGI so it never buffers the SSE stream) ----------

class TokenAuth:
    """If WEB_AUTH_TOKEN is set, require it on the API (data + actions) only. The static
    shell (/, /static, /health) loads WITHOUT a token — it holds no secrets — so that after
    one visit with ?token=… (which the page saves to localStorage) a later visit to the bare
    URL still works: a browser can't attach the token to a document navigation, but the
    page's own /api/* fetches carry it via the Authorization header. No token set → all open."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not path.startswith("/api/"):  # shell/static/health public; only the API is gated
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        sent = headers.get(b"authorization", b"").decode()
        qtok = parse_qs(scope.get("query_string", b"").decode()).get("token", [None])[0]
        ok = hmac.compare_digest(sent, f"Bearer {self.token}") or (
            qtok is not None and hmac.compare_digest(qtok, self.token))
        if ok:
            return await self.app(scope, receive, send)
        await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)


def build_app() -> Starlette:
    routes = [
        Route("/", index),
        Route("/health", health),
        Route("/api/config", api_config),
        Route("/api/status", api_status),
        Route("/api/settings", api_settings, methods=["POST"]),
        Route("/api/chat", api_chat, methods=["POST"]),
        Route("/api/approve", api_approve, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC)), name="static"),
    ]
    app = Starlette(routes=routes)
    _prune_convos()
    token = config.web_auth_token()
    if token:
        app.add_middleware(TokenAuth, token=token)
    return app


def main() -> None:
    if not config.anthropic_key():
        sys.exit("Set ANTHROPIC_API_KEY in .env (see config/.env.example) to run the web UI.")
    bind, port = config.web_bind(), config.web_port()
    # Refuse to expose the (money-spending) web UI on a non-loopback address with no token.
    if bind not in ("127.0.0.1", "localhost", "::1") and not config.web_auth_token():
        sys.exit(f"Refusing to bind {bind} without WEB_AUTH_TOKEN — that exposes the web UI "
                 f"unauthenticated. Set WEB_AUTH_TOKEN in .env, or use Tailscale Serve from "
                 f"127.0.0.1 (see docs/WEB-UI.md).")
    import uvicorn
    print(f"grocery-web → http://{bind}:{port}  (dry-run honored from .env)")
    # access_log=False: the auth token can arrive as ?token=… on the initial page load, and
    # uvicorn's access log would otherwise write it (and the URL) to a world-readable logfile.
    uvicorn.run(build_app(), host=bind, port=port, access_log=False)


if __name__ == "__main__":
    main()
