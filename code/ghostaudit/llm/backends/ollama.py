"""Ollama backend: local models through the HTTP API (standard library only, no key).

OLLAMA_HOST selects the server (default http://127.0.0.1:11434). Vision models take raster images only, so a
PDF raises and the adapter falls back to whatever the cache holds for that file. Local calls cost $0; models
tagged `:cloud` run on ollama.com and are labelled as such in the ledger.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import BackendResponse, BackendUnavailable

DEFAULT_HOST = "http://127.0.0.1:11434"
SEED = 7


def host_url() -> str:
    host = (os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip().rstrip("/")
    return host if host.startswith(("http://", "https://")) else f"http://{host}"


class OllamaBackend:
    def __init__(self, host: str | None = None, timeout: int = 600):
        self.host = (host or host_url()).rstrip("/")
        self.timeout = timeout
        try:
            with urllib.request.urlopen(f"{self.host}/api/version", timeout=3) as r:
                self.version = json.load(r).get("version", "")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BackendUnavailable(f"no Ollama server at {self.host} ({exc})") from exc

    def call(self, prompt: str, images: list[Path], model: str, max_tokens: int) -> BackendResponse:
        if any(Path(i).suffix.lower() == ".pdf" for i in images):
            raise ValueError("Ollama vision models take raster images, not PDFs")
        message = {"role": "user", "content": prompt}
        if images:
            message["images"] = [base64.b64encode(Path(i).read_bytes()).decode("ascii") for i in images]
        body = {"model": model, "stream": False, "messages": [message],
                "options": {"temperature": 0, "seed": SEED, "num_predict": max_tokens}}
        req = urllib.request.Request(f"{self.host}/api/chat", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        provider = "ollama (cloud)" if model.endswith(":cloud") else "ollama (local)"
        return BackendResponse(text=data["message"]["content"], tokens_in=int(data.get("prompt_eval_count") or 0),
                               tokens_out=int(data.get("eval_count") or 0), model=model, provider=provider,
                               cost_usd=0.0)
