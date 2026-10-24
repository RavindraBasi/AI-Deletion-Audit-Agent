"""Thin Claude wrapper: JSON-schema output, content-hash cache, usage ledger.

Thinking is switched to its lowest setting on both models: these are extraction and
classification calls where reasoning tokens would only add cost.
"""
import hashlib
import json
import os

from ..config import LLM_CACHE_DIR
from .usage import UsageLedger

THINKING_OFF = {"claude-haiku-5-5": {"type": "disabled"}, "claude-sonnet-5-5": {"type": "between_tools"}}


class LLM:
    def __init__(self, offline=False, refresh=False):
        self.offline = offline or not os.environ.get("ANTHROPIC_API_KEY")
        self.refresh = refresh
        self.usage = UsageLedger()
        self._client = None
        LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(max_retries=4)
        return self._client

    def json(self, purpose, model, system, content, schema, max_tokens):
        """Return the parsed JSON answer, or None when offline and not cached."""
        key = hashlib.sha256(json.dumps([model, system, content, schema], sort_keys=True).encode()).hexdigest()
        path = LLM_CACHE_DIR / f"{key}.json"
        if path.exists() and not self.refresh:
            self.usage.cache_hit(purpose)
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            return None
        try:
            response = self._call(model, system, content, schema, max_tokens)
        except Exception as error:   # one bad call must not abort a paid run
            print(f"[llm] {purpose} failed, continuing without it: {error}")
            return None
        if response.stop_reason in ("refusal", "max_tokens"):
            print(f"[llm] {purpose}: model stopped with {response.stop_reason}; answer skipped")
            self.usage.record(purpose, model, response.usage)
            return None
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        self.usage.record(purpose, model, response.usage)
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return data

    def _call(self, model, system, content, schema, max_tokens):
        return self.client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
            thinking=THINKING_OFF[model],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
