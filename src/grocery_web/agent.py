"""The web UI's brain: a Claude tool-use agent loop bridged to the grocery-gateway.

For each turn we open an in-process gateway client (Client(build_gateway())), expose its
tools to the Anthropic Messages API, stream the reply, dispatch tool_use blocks back to
the gateway, and repeat until the model stops. place_order outcomes are surfaced as
structured events so the UI can render an Approve button and outcome banners.

run_chat / approve_order are async generators yielding {"event","data"} dicts. They
mutate the passed-in `messages` list in place, so the caller persists it after draining.
"""

import json
import re

from anthropic import AsyncAnthropic
from fastmcp import Client

from heb_checkout import config as core
from heb_checkout.gateway import build_gateway
from . import config

MAX_TOKENS = 8000
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # Anthropic tool-name constraint


def _system_prompt() -> str:
    base = ""
    p = core.agent_home() / "prompts" / "system-prompt.md"
    if p.exists():
        base = p.read_text()
    return base + (
        "\n\n## Web chat\n"
        "You are in a web chat. When place_order returns status 'needs_approval', STOP "
        "and present the cart summary and total — the UI shows an Approve button; do NOT "
        "call place_order again yourself. The user clicks Approve to confirm. Use "
        "recall_item/get_preferences to reuse the user's remembered picks, and "
        "remember_item when they choose a product."
    )


def _content_text(content) -> str:
    parts = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts) or "(no text)"


def _dump_block(block) -> dict:
    """Reduce a streamed content block to the minimal dict the API accepts back, so we can
    both resend it for tool-use continuity and persist it as plain JSON."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    return block.model_dump()


async def _anthropic_tools(gw: Client):
    """MCP tools → Anthropic tool schemas. Returns (tools, name_map). Excludes tools on the
    web-agent denylist (money-safety: no autonomous policy/wallet mutation) and any tool
    whose name violates the Anthropic name constraint (would 400 the whole request)."""
    denied = config.denied_tools()
    tools, name_map = [], {}
    for t in await gw.list_tools():
        if t.name in denied or not _VALID_TOOL_NAME.match(t.name or ""):
            continue
        schema = t.inputSchema or {"type": "object", "properties": {}}
        tools.append({"name": t.name, "description": t.description or "", "input_schema": schema})
        name_map[t.name] = t.name
    return tools, name_map


def _surface_outcome(tool_name: str, payload):
    """Yield structured UI events for place_order outcomes (Approve button, banners)."""
    if tool_name != "place_order" or not isinstance(payload, dict):
        return
    status = payload.get("status")
    if status == "needs_approval":
        yield {"event": "needs_approval", "data": {
            "approval_id": payload.get("approval_id"),
            "order_total": payload.get("order_total"),
            "items": payload.get("items") or [],
            "fulfillment": payload.get("fulfillment"),
            "slot_text": payload.get("slot_text"),
            "expires_at": payload.get("expires_at"),
            "reason": payload.get("reason"),
        }}
    elif status in ("placed", "placed_unconfirmed"):
        yield {"event": "order_outcome", "data": {
            "status": status,
            "total": payload.get("estimated_total"),
            "confirmation": payload.get("confirmation"),
            "unconfirmed": status == "placed_unconfirmed",
        }}
    elif status == "dry_run":
        yield {"event": "order_outcome", "data": {
            "status": "dry_run",
            "total": payload.get("estimated_total"),
            "note": "Checkout rehearsed, not charged (dry-run mode).",
        }}
    elif status == "duplicate_skipped":
        yield {"event": "order_outcome", "data": {
            "status": "duplicate_skipped", "reason": payload.get("reason"),
        }}
    elif status in ("blocked", "aborted", "error"):
        yield {"event": "order_outcome", "data": {
            "status": status, "reason": payload.get("reason"),
        }}


async def _call_tool(gw: Client, name: str, args: dict):
    res = await gw.call_tool(name, args or {}, raise_on_error=False)
    if res.is_error:
        return {"error": _content_text(res.content)}, True
    data = res.data
    if data is None:
        data = {"result": _content_text(res.content)}
    return data, False


async def run_chat(messages: list, model: str):
    """Run the agent loop over `messages` (mutated in place). Yields SSE event dicts."""
    client = AsyncAnthropic()
    system = _system_prompt()
    async with Client(build_gateway()) as gw:
        tools, name_map = await _anthropic_tools(gw)
        while True:
            async with client.messages.stream(
                model=model, max_tokens=MAX_TOKENS, system=system,
                tools=tools, messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"event": "assistant_delta", "data": {"text": text}}
                final = await stream.get_final_message()

            messages.append({"role": "assistant", "content": [_dump_block(b) for b in final.content]})
            yield {"event": "assistant_done", "data": {}}

            if final.stop_reason != "tool_use":
                yield {"event": "turn_end", "data": {}}
                return

            tool_results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                real = name_map.get(block.name, block.name)
                yield {"event": "tool_call", "data": {"name": real, "input": block.input}}
                payload, is_error = await _call_tool(gw, real, block.input or {})
                for ev in _surface_outcome(real, payload):
                    yield ev
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, default=str),
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})


async def approve_order(messages: list, approval_id: str, model: str):
    """The Approve button: call place_order(approval_id) DIRECTLY (one human yes → exactly
    one locked place_order), surface the outcome, then have the model narrate it."""
    async with Client(build_gateway()) as gw:
        payload, _ = await _call_tool(gw, "place_order", {"approval_id": approval_id})
    for ev in _surface_outcome("place_order", payload):
        yield ev
    messages.append({"role": "user", "content": (
        f"[system] The user approved and place_order ran for approval {approval_id}. "
        f"Result: {json.dumps(payload, default=str)}. Briefly confirm to the user what happened."
    )})
    async for ev in run_chat(messages, model):
        yield ev
