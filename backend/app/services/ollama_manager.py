"""Keeps the offline AI (Ollama) ready: finds it, points it at the right model folder,
starts it, and downloads models with progress. Process and registry access are
injectable so this can be tested without Ollama or Windows.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from .. import config
from . import ollama_stores

log = logging.getLogger(__name__)
DEFAULT_MODEL = "qwen2.5vl:7b"


# ---------------- finding and running Ollama ----------------

def find_exe() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    candidates = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates += [Path(local) / "Programs" / "Ollama" / "ollama.exe",
                       Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama")]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def find_tray_app() -> str | None:
    if sys.platform != "win32":
        return None
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    return str(p) if p.exists() else None


def _http() -> httpx.Client:
    # Ollama is local: never route it through a system/corporate proxy.
    return httpx.Client(timeout=5, trust_env=False)


def base_url() -> str:
    return config.get_settings().ollama_url.rstrip("/")


def running_models(http: httpx.Client | None = None) -> set[str] | None:
    """Model names the running Ollama can see, or None if Ollama isn't answering."""
    try:
        r = (http or _http()).get(f"{base_url()}/api/tags", timeout=3)
        r.raise_for_status()
        return {ollama_stores.normalize(m["name"]) for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError):
        return None


class ProcessControl:
    """Starting/stopping processes. Swapped for a fake in tests."""

    def start(self, args: list[str], env: dict) -> None:
        kwargs: dict = {"env": env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                        "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(args, **kwargs)

    def stop_ollama(self) -> None:
        if sys.platform == "win32":
            for image in ("ollama app.exe", "ollama.exe"):
                subprocess.run(["taskkill", "/IM", image, "/F"], capture_output=True)
        else:
            subprocess.run(["pkill", "-x", "ollama"], capture_output=True)


PROCESS = ProcessControl()


def _env_for(models_dir: str | None) -> dict:
    env = os.environ.copy()
    if models_dir:
        env["OLLAMA_MODELS"] = models_dir
    return env


def start(models_dir: str | None = None, wait: float = 20.0, http: httpx.Client | None = None) -> bool:
    """Start Ollama (tray app on Windows if present, else `ollama serve`) and wait until it answers."""
    if running_models(http) is not None:
        return True
    models_dir = models_dir or config.get_settings().ollama_models_dir or None
    tray, exe = find_tray_app(), find_exe()
    if not tray and not exe:
        return False
    args = [tray] if tray else [exe, "serve"]
    log.info("Starting Ollama: %s (models: %s)", args, models_dir or "default")
    PROCESS.start(args, _env_for(models_dir))
    deadline = time.time() + wait
    while time.time() < deadline:
        if running_models(http) is not None:
            return True
        time.sleep(0.5)
    return False


def ensure_running(http: httpx.Client | None = None) -> bool:
    """Used before analysing: start Ollama if it's installed but not running."""
    if running_models(http) is not None:
        return True
    return start(http=http)


def restart(models_dir: str, http: httpx.Client | None = None) -> bool:
    PROCESS.stop_ollama()
    for _ in range(20):
        if running_models(http) is None:
            break
        time.sleep(0.25)
    return start(models_dir, http=http)


# ---------------- system-wide OLLAMA_MODELS ----------------

def set_user_env(name: str, value: str, winreg_mod=None, broadcast: bool = True) -> str:
    """Set a per-user environment variable permanently. Returns what was done."""
    if sys.platform != "win32" and winreg_mod is None:
        return (f'Add this line to your shell profile (e.g. ~/.zshrc or ~/.bashrc): '
                f'export {name}="{value}"')
    if winreg_mod is None:
        import winreg as winreg_mod  # type: ignore
    key = winreg_mod.OpenKey(winreg_mod.HKEY_CURRENT_USER, "Environment", 0, winreg_mod.KEY_SET_VALUE)
    try:
        winreg_mod.SetValueEx(key, name, 0, winreg_mod.REG_SZ, value)
    finally:
        winreg_mod.CloseKey(key)
    os.environ[name] = value
    if broadcast and sys.platform == "win32":
        try:  # tell Explorer & new programs the environment changed
            import ctypes
            result = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                                    ctypes.byref(result))
        except Exception:
            pass
    return f"Set {name}={value} for your Windows user"


# ---------------- choosing the model folder ----------------

def plan(http: httpx.Client | None = None) -> dict:
    """What folder/model to use and whether the running Ollama matches it."""
    s = config.get_settings()
    stores, notes = ollama_stores.discover()
    pinned = s.ollama_models_dir
    if pinned and any(os.path.normcase(st.path) == os.path.normcase(pinned) for st in stores):
        st = next(st for st in stores if os.path.normcase(st.path) == os.path.normcase(pinned))
        if st.has(s.ollama_model):
            choice = ollama_stores.Choice(st.path, s.ollama_model, False, f"Using {st.path}")
        else:
            choice = ollama_stores.choose([st] + [x for x in stores if x is not st], s.ollama_model)
    else:
        choice = ollama_stores.choose(stores, s.ollama_model, fallback_dir=pinned or None)
    running = running_models(http)
    mismatch = False
    if running is not None and choice.store:
        wanted = ollama_stores.store_model_names(choice.store)
        # Ollama is serving a different folder if it can't see models the chosen folder has.
        mismatch = bool(wanted) and not wanted.issubset(running)
    return {
        "stores": [st.to_dict() for st in stores],
        "notes": notes,
        "choice": choice.__dict__,
        "installed": bool(find_exe() or find_tray_app()),
        "running": running is not None,
        "running_models": sorted(running or []),
        "mismatch": mismatch,
        "env_models_dir": os.environ.get("OLLAMA_MODELS", ""),
    }


def apply_choice(store: str | None, model: str, system_wide: bool = True, winreg_mod=None) -> list[str]:
    """Save the choice, set OLLAMA_MODELS system-wide, and return what was done."""
    done = []
    config.save_settings({"ollama_models_dir": store or "", "ollama_model": model})
    done.append(f"Clip Manager will use {model}" + (f" from {store}" if store else ""))
    if store and system_wide:
        try:
            done.append(set_user_env("OLLAMA_MODELS", store, winreg_mod=winreg_mod))
        except OSError as e:
            done.append(f"Couldn't set OLLAMA_MODELS ({e}); Clip Manager will still use {store}")
    return done


# ---------------- downloading models ----------------

_pull_lock = threading.Lock()
PULL: dict = {"model": None, "status": "idle", "percent": 0.0, "error": None, "done": False}


def pull_state() -> dict:
    return dict(PULL)


def pull(model: str, http: httpx.Client | None = None) -> dict:
    """Download a model through the running Ollama, tracking progress in PULL."""
    with _pull_lock:
        PULL.update(model=model, status="starting", percent=0.0, error=None, done=False)
        totals: dict[str, int] = {}
        done: dict[str, int] = {}
        client = http or httpx.Client(timeout=None, trust_env=False)
        try:
            with client.stream("POST", f"{base_url()}/api/pull", json={"model": model, "stream": True}) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"Ollama answered {r.status_code}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    digest = msg.get("digest")
                    if digest and msg.get("total"):
                        totals[digest] = int(msg["total"])
                        done[digest] = int(msg.get("completed") or 0)
                    total = sum(totals.values())
                    PULL.update(status=msg.get("status", ""),
                                percent=round(100 * sum(done.values()) / total, 1) if total else PULL["percent"])
                    if msg.get("status") == "success":
                        PULL.update(percent=100.0, done=True)
            if not PULL["done"]:
                raise RuntimeError("The download stopped before finishing")
        except Exception as e:  # network drop, disk full, unknown model...
            PULL.update(status="failed", error=str(e), done=True)
        return dict(PULL)


def pull_in_background(model: str) -> None:
    if PULL["status"] not in ("idle", "failed") and not PULL["done"]:
        return
    threading.Thread(target=pull, args=(model,), daemon=True).start()


def status(http: httpx.Client | None = None) -> dict:
    out = plan(http)
    out["pull"] = pull_state()
    out["model"] = config.get_settings().ollama_model
    return out
