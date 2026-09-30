"""App settings, stored as JSON in the data directory and editable from the UI."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TCM_DATA_DIR", ROOT_DIR / "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"

# Fields never sent back to the browser in full.
SECRET_FIELDS = ("twitch_client_secret", "anthropic_api_key")


class Settings(BaseModel):
    # Twitch (free app from dev.twitch.tv/console)
    twitch_client_id: str = ""
    twitch_client_secret: str = ""

    # Which AI does the watching: "local" = Ollama on your GPU, "claude" = online.
    ai_mode: Literal["local", "claude"] = "local"

    # Local (offline)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:7b"
    # Existing Ollama model folders to look in, in order of preference.
    ollama_model_dirs: list[str] = [
        r"L:\.DoNotTouch\models\.ollama",
        r"J:\ai\ollama_models",
    ]
    # The model folder chosen for Ollama ("" = let the app pick).
    ollama_models_dir: str = ""

    # Online
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high"] = "low"
    # Re-check low-confidence local results with Claude (needs a key).
    claude_cross_check: bool = False

    # Speech-to-text (always local)
    whisper_model: str = "small"
    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    whisper_gpu_index: int = 0

    # Pipeline
    frames_per_clip: int = 6
    auto_analyze_new_clips: bool = True
    background_recheck: bool = True
    review_threshold: float = 0.6
    library_dir: str = str(ROOT_DIR / "library")

    # Resolve
    resolve_bin_root: str = "Twitch Clips"

    # First-run setup wizard finished
    setup_complete: bool = False


_lock = threading.Lock()
_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    with _lock:
        if _cached is None:
            if SETTINGS_PATH.exists():
                _cached = Settings.model_validate_json(SETTINGS_PATH.read_text("utf-8"))
            else:
                _cached = Settings()
            # Environment variables win, handy for a .env or CI.
            env_map = {
                "TWITCH_CLIENT_ID": "twitch_client_id",
                "TWITCH_CLIENT_SECRET": "twitch_client_secret",
                "ANTHROPIC_API_KEY": "anthropic_api_key",
            }
            for env, field in env_map.items():
                if os.environ.get(env) and not getattr(_cached, field):
                    setattr(_cached, field, os.environ[env])
        return _cached


def save_settings(patch: dict) -> Settings:
    global _cached
    current = get_settings().model_dump()
    for key, value in patch.items():
        if key not in current:
            continue
        # The UI sends masked secrets back unchanged; ignore those.
        if key in SECRET_FIELDS and isinstance(value, str) and value.startswith("••"):
            continue
        current[key] = value
    new = Settings.model_validate(current)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(new.model_dump(), indent=2), "utf-8")
    with _lock:
        _cached = new
    return new


def public_settings() -> dict:
    data = get_settings().model_dump()
    for key in SECRET_FIELDS:
        value = data[key]
        data[key] = ("••••" + value[-4:]) if value else ""
    return data
