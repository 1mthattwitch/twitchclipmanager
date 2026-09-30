"""Offline provider: a vision model served by Ollama on your own GPU."""
from __future__ import annotations

from pathlib import Path

import httpx

from .. import config
from .base import AIError, Provider, b64, parse_json_loose


class OllamaProvider(Provider):
    name = "local"

    def __init__(self, http: httpx.Client | None = None):
        s = config.get_settings()
        self.url = s.ollama_url.rstrip("/")
        self.model = s.ollama_model
        # Ollama is local: never route it through a system/corporate proxy.
        self.http = http or httpx.Client(timeout=600, trust_env=False)

    @property
    def label(self) -> str:
        return f"local:{self.model}"

    def available(self) -> tuple[bool, str]:
        try:
            r = self.http.get(f"{self.url}/api/tags", timeout=3)
            names = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return False, "Ollama isn't running. Start it, or switch to Claude in Settings."
        base = self.model.split(":")[0]
        if not any(n == self.model or n.split(":")[0] == base for n in names):
            return False, f"Model {self.model} isn't downloaded. Run: ollama pull {self.model}"
        return True, "ok"

    def _chat(self, system: str, prompt: str, images: list[Path], fmt: dict | None) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt, "images": [b64(p) for p in images]},
            ],
            "options": {"temperature": 0.2, "num_ctx": 16384},
        }
        if fmt is not None:
            body["format"] = fmt
        try:
            r = self.http.post(f"{self.url}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise AIError(f"Couldn't reach Ollama at {self.url}: {e}", transient=True) from e
        if r.status_code != 200:
            raise AIError(f"Ollama error {r.status_code}: {r.text[:300]}", transient=r.status_code >= 500)
        return r.json()["message"]["content"]

    def generate_json(self, system: str, prompt: str, images: list[Path], schema: dict) -> dict:
        return parse_json_loose(self._chat(system, prompt, images, schema))

    def generate_text(self, system: str, prompt: str, images: list[Path]) -> str:
        return self._chat(system, prompt, images, None).strip()
