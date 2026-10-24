"""Optional LLM assistance (Anthropic Messages API over plain HTTPS, no SDK needed).

Used only where deterministic parsing is weakest:
  * classify_intent - request texts the multilingual rules cannot classify confidently
  * read_image      - transcribe identifiers/resource ids from product screenshots
Every response is cached on disk (keyed by prompt hash) so re-runs cost nothing,
and token usage is accounted for evaluation/usage_report.md.

Configuration (environment only - never hard-code keys):
  ANTHROPIC_API_KEY   enables the client
  GHOST_LLM_MODEL     model name (default: claude-haiku-4-5)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# USD per million tokens (input, output) - used for cost estimates only
PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-5": (3.0, 15.0)}


def _prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as fh:
        return fh.read()


class LLM:
    def __init__(self, api_key: str, model: str, cache_dir: str):
        self.key = api_key
        self.model = model
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.calls = 0
        self.cached = 0
        self.in_tokens = 0
        self.out_tokens = 0

    def _call(self, system: str, content: list, max_tokens: int = 300) -> str:
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "system": system, "messages": [{"role": "user", "content": content}]}
        raw = json.dumps(body, sort_keys=True)
        h = hashlib.sha256(raw.encode()).hexdigest()
        path = os.path.join(self.cache_dir, h + ".json")
        if os.path.exists(path):
            self.cached += 1
            data = json.load(open(path, encoding="utf-8"))
        else:
            req = urllib.request.Request(
                os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"),
                data=raw.encode(), method="POST",
                headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read())
            except Exception:
                return ""
            json.dump(data, open(path, "w", encoding="utf-8"))
            self.calls += 1
            u = data.get("usage", {})
            self.in_tokens += int(u.get("input_tokens", 0))
            self.out_tokens += int(u.get("output_tokens", 0))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def classify_intent(self, text: str) -> str:
        out = self._call(_prompt("intent_classifier.md"), [{"type": "text", "text": text}], 20)
        out = out.strip().upper()
        if out.startswith("DELETE"):
            return "DELETE"
        if out.startswith("OTHER"):
            return "OTHER"
        return ""

    def read_image(self, path: str) -> str:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                   {"type": "text", "text": "Transcribe the identifiers in this screenshot."}]
        return self._call(_prompt("screenshot_reader.md"), content, 800)

    def usage_summary(self):
        pin, pout = PRICES.get(self.model, (3.0, 15.0))
        cost = self.in_tokens / 1e6 * pin + self.out_tokens / 1e6 * pout
        return {"provider": "Anthropic", "model": self.model, "calls": self.calls, "cache_hits": self.cached,
                "input_tokens": self.in_tokens, "output_tokens": self.out_tokens, "est_cost_usd": round(cost, 6)}

    def write_usage(self, path, n_requests):
        s = self.usage_summary()
        s["requests"] = n_requests
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(s, open(path, "w"), indent=2)


def from_env(cache_dir: str):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return LLM(key, os.environ.get("GHOST_LLM_MODEL", "claude-haiku-4-5"), cache_dir)
