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


# ---------- conversation store (per-restart durable; chat threads on disk) ----------

# One asyncio.Lock per conversation id so two concurrent requests for the same thread
# (two tabs, or a chat turn racing the Approve button) can't lost-update the history via
# read-modify-write. Single-process uvicorn, so an in-process dict is sufficient.
_cid_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _safe_cid(cid: str) -> str:
    cid = re.sub(r"[^a-zA-Z0-9_-]", "", cid or "")
    return (cid or uuid.uuid4().hex[:12])[:64]


def _load_convo(cid: str) -> dict:
    path = config.conversations_dir() / f"{cid}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"messages": []}


def _save_convo(cid: str, convo: dict) -> None:
    """Atomic write so a crash / disconnect mid-write can't truncate a thread (which
    _load_convo would then silently discard)."""
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
    return FileResponse(STATIC / "index.html")


async def api_config(request):
    return JSONResponse({
        "models": config.model_choices(),
        "default_model": config.default_alias(),
        "auth_required": bool(config.web_auth_token()),
    })


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
    auth = core.auth_state_path()
    return JSONResponse({
        "dry_run": core.dry_run_default(),
        "heb_session_present": auth.exists(),
        "policy_mode": mode,
        "spent_last_7_days": spent7,
    })


async def health(request):
    from heb_checkout import config as core
    auth = core.auth_state_path()
    return JSONResponse({"ok": auth.exists(), "dry_run_mode": core.dry_run_default()})


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
    """If WEB_AUTH_TOKEN is set, require it on every request (Bearer header or ?token=).
    /health stays open. With no token set (local default) everything is open."""

    OPEN = ("/health",)

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in self.OPEN:
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
        Route("/api/chat", api_chat, methods=["POST"]),
        Route("/api/approve", api_approve, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC)), name="static"),
    ]
    app = Starlette(routes=routes)
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
    uvicorn.run(build_app(), host=bind, port=port)


if __name__ == "__main__":
    main()
