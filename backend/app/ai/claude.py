"""Online provider: Claude via the Anthropic SDK."""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from .. import config
from .base import AIError, Provider, b64

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, client: anthropic.Anthropic | None = None):
        s = config.get_settings()
        if client is None and not s.anthropic_api_key:
            raise AIError("Add an Anthropic API key in Settings to use Claude.")
        self.client = client or anthropic.Anthropic(api_key=s.anthropic_api_key)
        self.model = s.claude_model
        self.effort = s.claude_effort
        self._fallbacks_ok = True

    @property
    def label(self) -> str:
        return f"claude:{self.model}"

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def _content(self, prompt: str, images: list[Path]) -> list[dict]:
        blocks: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(p)}}
            for p in images
        ]
        blocks.append({"type": "text", "text": prompt})
        return blocks

    def _create(self, system: str, prompt: str, images: list[Path], fmt: dict | None):
        output_config: dict = {"effort": self.effort}
        if fmt is not None:
            output_config["format"] = {"type": "json_schema", "schema": fmt}
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": self._content(prompt, images)}],
            output_config=output_config,
        )
        try:
            if self._fallbacks_ok:
                try:
                    # If a safety classifier declines, let the API retry on a fallback model.
                    return self.client.beta.messages.create(
                        betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
                except (anthropic.BadRequestError, TypeError):
                    # Model/account/SDK doesn't accept fallbacks: retry plainly below. If the plain
                    # request succeeds, stop sending fallbacks; if it fails too, the error is real.
                    response = self.client.messages.create(**kwargs)
                    self._fallbacks_ok = False
                    return response
            return self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AIError("Claude rejected the API key. Check Settings.") from e
        except anthropic.RateLimitError as e:
            raise AIError("Claude rate limit hit; the clip will be retried.", transient=True) from e
        except anthropic.APIStatusError as e:
            raise AIError(f"Claude API error {e.status_code}: {e.message}", transient=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise AIError("Couldn't reach Claude. Are you offline? Switch to Local in Settings.", transient=True) from e

    @staticmethod
    def _text(response) -> str:
        if response.stop_reason == "refusal":
            raise AIError("Claude declined to describe this clip.")
        return "".join(b.text for b in response.content if b.type == "text")

    def generate_json(self, system: str, prompt: str, images: list[Path], schema: dict) -> dict:
        text = self._text(self._create(system, prompt, images, schema))
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise AIError(f"Claude returned invalid JSON: {text[:200]}") from e

    def generate_text(self, system: str, prompt: str, images: list[Path]) -> str:
        return self._text(self._create(system, prompt, images, None)).strip()
