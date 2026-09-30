"""Provider interface: every AI backend takes text + images and returns JSON or text."""
from __future__ import annotations

import base64
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path


class AIError(RuntimeError):
    pass


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_json(self, system: str, prompt: str, images: list[Path], schema: dict) -> dict:
        ...

    @abstractmethod
    def generate_text(self, system: str, prompt: str, images: list[Path]) -> str:
        ...

    def available(self) -> tuple[bool, str]:
        return True, "ok"


def b64(path: Path) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def parse_json_loose(text: str) -> dict:
    """Parse model output that should be JSON but may be wrapped in prose or fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise AIError(f"Model did not return JSON: {text[:200]}")


def get_provider(name: str | None = None) -> Provider:
    from .. import config
    from .claude import ClaudeProvider
    from .ollama import OllamaProvider

    name = name or config.get_settings().ai_mode
    if name == "claude":
        return ClaudeProvider()
    if name == "local":
        return OllamaProvider()
    raise AIError(f"Unknown AI mode {name!r}")
