"""A plain-text report of everything relevant, to copy and send when something goes wrong."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from .. import config, db

SECRET_KEYS = ("twitch_client_secret", "anthropic_api_key")


def _tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return "(not found)"
    return "\n".join(text[-lines:]) or "(empty)"


def _git_version() -> str:
    try:
        return subprocess.run(["git", "-C", str(config.ROOT_DIR), "log", "-1", "--format=%h %cd", "--date=short"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def app_log_path() -> Path:
    return config.DATA_DIR / "app.log"


def install_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ClipManager" / "install.log"


def report(status: dict | None = None) -> str:
    from . import ollama_manager, resolve_setup
    s = config.public_settings()
    for k in SECRET_KEYS:
        s[k] = "(set)" if s.get(k) else "(empty)"
    parts = [
        "=== Clip Manager diagnostics ===",
        f"App version: {_git_version()}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"System: {platform.platform()}",
        f"App folder: {config.ROOT_DIR}",
        f"Data folder: {config.DATA_DIR}",
        "",
        "--- Settings ---",
        *(f"{k}: {v}" for k, v in sorted(s.items())),
        "",
        "--- Status ---",
        *(f"{k}: {v}" for k, v in (status or {}).items()),
    ]
    try:
        o = ollama_manager.plan()
        parts += ["", "--- Offline AI (Ollama) ---",
                  f"installed: {o['installed']}  running: {o['running']}  mismatch: {o['mismatch']}",
                  f"choice: {o['choice']}", f"OLLAMA_MODELS: {o['env_models_dir'] or '(not set)'}"]
        for st in o["stores"]:
            names = ", ".join(m["name"] + ("" if m["complete"] else " (incomplete)") for m in st["models"]) or "none"
            parts.append(f"store {st['path']}: {names}")
        parts += [f"note: {n}" for n in o["notes"]]
    except Exception as e:
        parts.append(f"(Ollama check failed: {e})")
    try:
        parts += ["", "--- Resolve ---", *(f"{k}: {v}" for k, v in resolve_setup.script_status().items())]
    except Exception as e:
        parts.append(f"(Resolve check failed: {e})")
    try:
        errors = db.query("SELECT kind, clip_id, message, updated_at FROM jobs WHERE status='error' "
                          "ORDER BY updated_at DESC LIMIT 10")
        lines = [f"{e['kind']} {e['clip_id'] or ''}: {e['message']}" for e in errors] or ["none"]
        parts += ["", "--- Recent failed jobs ---", *lines]
    except Exception as e:
        parts.append(f"(job check failed: {e})")
    parts += ["", "--- App log (last 80 lines) ---", _tail(app_log_path(), 80),
              "", "--- Install log (last 40 lines) ---", _tail(install_log_path(), 40)]
    return "\n".join(str(p) for p in parts)
