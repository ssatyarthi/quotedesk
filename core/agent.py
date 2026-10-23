"""Minimal tool-use loop shared by the RFx co-pilot and the analyst."""
import json

from .llm import LLM


def _clean(block):
    d = block.model_dump(exclude_none=True)
    # keep only fields the API accepts back
    if d.get("type") == "text":
        return {"type": "text", "text": d["text"]}
    if d.get("type") == "tool_use":
        return {"type": "tool_use", "id": d["id"], "name": d["name"], "input": d["input"]}
    return d


def run(llm: LLM, system: str, messages: list, tools: list, handlers: dict, max_steps=14, tag="agent", on_step=None):
    """
    messages: API-format history; new assistant/tool messages are appended in place.
    handlers: tool name -> fn(**input) returning str (shown to the model).
    Returns (final_text, trace) where trace lists every tool call made this turn.
    """
    trace = []
    for _ in range(max_steps):
        resp = llm.create(system=system, messages=messages, tools=tools, max_tokens=6000, tag=tag)
        blocks = [_clean(b) for b in resp.content if b.type in ("text", "tool_use")]
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b["type"] == "tool_use"]
        if not uses:
            return "\n".join(b["text"] for b in blocks if b["type"] == "text").strip(), trace
        results = []
        for u in uses:
            fn = handlers.get(u["name"])
            try:
                out = fn(**u["input"]) if fn else f"Unknown tool {u['name']}"
                is_err = False
            except Exception as e:
                out, is_err = f"ERROR: {e}", True
            out = out if isinstance(out, str) else json.dumps(out, default=str)
            trace.append(dict(tool=u["name"], input=u["input"], output=out[:4000], error=is_err))
            if on_step:
                on_step(u["name"], u["input"])
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": out[:20000], "is_error": is_err})
        messages.append({"role": "user", "content": results})
    return "I stopped after too many steps - try narrowing the question.", trace
