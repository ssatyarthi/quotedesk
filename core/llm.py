"""Thin wrapper over the Anthropic Messages API: model fallback, usage logging."""
import json
import time
from datetime import datetime

import anthropic

from . import config

# rough list prices, USD per million tokens (input, output) - for the on-screen cost meter only
PRICES = {"opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, api_key: str | None = None):
        key = api_key or config.API_KEY
        if not key:
            raise LLMError("No ANTHROPIC_API_KEY found. Put it in the .env file (see README).")
        self.client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)
        self.model = None
        self.structured_mode = {}

    def _pick_model(self):
        if self.model:
            return self.model
        last = None
        for m in config.MODEL_CANDIDATES:
            try:
                self.client.messages.create(model=m, max_tokens=5, messages=[{"role": "user", "content": "ping"}])
                self.model = m
                return m
            except anthropic.NotFoundError as e:
                last = e
                continue
            except anthropic.AuthenticationError as e:
                raise LLMError("The API key was rejected. Check ANTHROPIC_API_KEY in .env.") from e
        raise LLMError(f"None of the models {config.MODEL_CANDIDATES} are available to this key: {last}")

    def create(self, *, system, messages, tools=None, tool_choice=None, max_tokens=8000, tag=""):
        model = self._pick_model()
        forced = tool_choice and tool_choice.get("type") == "tool"
        # Some models don't accept a forced tool call; for those, ask for the same JSON schema as structured output.
        if forced and self.structured_mode.get(model) == "output_config":
            return self._create_structured(model, system, messages, tools, tool_choice["name"], max_tokens, tag)
        kw = dict(model=model, system=system, messages=messages, max_tokens=max_tokens)
        if tools:
            kw["tools"] = tools
        if tool_choice:
            kw["tool_choice"] = tool_choice
        t0 = time.time()
        try:
            resp = self.client.messages.create(**kw)
        except anthropic.BadRequestError as e:
            if forced and "tool_choice" in str(e):
                self.structured_mode[model] = "output_config"
                return self._create_structured(model, system, messages, tools, tool_choice["name"], max_tokens, tag)
            raise LLMError(f"API rejected the request: {e}") from e
        self._log(model, resp, time.time() - t0, tag)
        return resp

    def _create_structured(self, model, system, messages, tools, name, max_tokens, tag):
        """Forced tool calls aren't supported on this model: offer only that tool, ask for it, retry once if skipped."""
        tool = next(t for t in tools if t["name"] == name)
        sys_ = system + f"\n\nYou must answer by calling the {name} tool exactly once. Do not answer in plain text."
        msgs = list(messages)
        for attempt in range(2):
            t0 = time.time()
            try:
                resp = self.client.messages.create(model=model, system=sys_, messages=msgs, max_tokens=max_tokens,
                                                   tools=[tool], tool_choice={"type": "auto"})
            except anthropic.BadRequestError as e:
                raise LLMError(f"API rejected the request: {e}") from e
            self._log(model, resp, time.time() - t0, tag)
            if any(b.type == "tool_use" and b.name == name for b in resp.content):
                return resp
            msgs = msgs + [{"role": "assistant", "content": [{"type": "text", "text": "".join(
                b.text for b in resp.content if b.type == "text") or "(no answer)"}]},
                           {"role": "user", "content": f"Please record your answer by calling the {name} tool now."}]
        return resp

    @staticmethod
    def _log(model, resp, secs, tag):
        u = getattr(resp, "usage", None)
        if not u:
            return
        fam = next((k for k in PRICES if k in model), "sonnet")
        pin, pout = PRICES[fam]
        cost = (u.input_tokens * pin + u.output_tokens * pout) / 1e6
        rec = dict(ts=datetime.now().isoformat(timespec="seconds"), tag=tag, model=model,
                   input_tokens=u.input_tokens, output_tokens=u.output_tokens, usd=round(cost, 4), secs=round(secs, 1))
        with open(config.USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def usage_summary():
    if not config.USAGE_LOG.exists():
        return 0, 0.0
    n, usd = 0, 0.0
    for line in config.USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            n += 1
            usd += r.get("usd", 0)
        except Exception:
            pass
    return n, usd


class _Structured:
    """Makes a structured-output response look like a forced tool call to the rest of the code."""
    def __init__(self, resp, name, data):
        self.raw, self.name, self.data = resp, name, data
        self.stop_reason = resp.stop_reason
        self.usage = resp.usage
        self.content = resp.content


def _strict(schema):
    """Structured outputs need every object closed and every field required (few optional fields allowed)."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object" or (isinstance(out.get("type"), list) and "object" in out["type"]):
            out.setdefault("additionalProperties", False)
            if "properties" in out:  # every field required; "not given" is expressed as "" or null
                out["required"] = list(out["properties"])
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


def tool_input(resp, name):
    if isinstance(resp, _Structured):
        if resp.name == name:
            return resp.data
        raise LLMError(f"Expected '{name}' structure, got '{resp.name}'.")
    for b in resp.content:
        if b.type == "tool_use" and b.name == name:
            return b.input
    raise LLMError(f"Model did not return the expected '{name}' structure (stop_reason={resp.stop_reason}).")
