"""Installs the "Clip Manager" script into DaVinci Resolve's Scripts menu and checks the connection."""
from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

from .. import config, resolve_bridge

SCRIPT_SOURCE = config.ROOT_DIR / "resolve" / "Clip Manager.py"


def scripts_dir() -> Path:
    """Resolve's per-user Utility scripts folder (shows under Workspace > Scripts)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve"
                / "Fusion" / "Scripts" / "Utility")
    return Path.home() / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility"


def resolve_installed() -> bool:
    if sys.platform == "win32":
        app = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blackmagic Design" / "DaVinci Resolve" / "Resolve.exe"
        support = Path(os.environ.get("APPDATA", "")) / "Blackmagic Design" / "DaVinci Resolve"
        return app.exists() or support.exists()
    if sys.platform == "darwin":
        return Path("/Applications/DaVinci Resolve/DaVinci Resolve.app").exists()
    return Path("/opt/resolve/bin/resolve").exists()


def script_status() -> dict:
    target = scripts_dir() / SCRIPT_SOURCE.name
    installed = target.exists()
    current = installed and target.read_bytes() == SCRIPT_SOURCE.read_bytes()
    return {"resolve_installed": resolve_installed(), "script_installed": installed,
            "script_current": current, "script_path": str(target)}


def install_script() -> dict:
    target_dir = scripts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCRIPT_SOURCE, target_dir / SCRIPT_SOURCE.name)
    return script_status()


def check_connection(timeout: float = 8.0) -> dict:
    """Try to talk to a running Resolve Studio (in a thread, so a hang can't block the app)."""
    result: dict = {"ok": False, "message": "Timed out talking to Resolve."}

    def attempt():
        try:
            res = resolve_bridge.get_resolve()
            project = res.GetProjectManager().GetCurrentProject()
            name = project.GetName() if project else None
            result.update(ok=True, message="Connected to Resolve" + (f" (project: {name})" if name else
                                                                         " (open a project to import clips)"))
        except resolve_bridge.ResolveError as e:
            result.update(ok=False, message=str(e))
        except Exception as e:  # unexpected API behaviour
            result.update(ok=False, message=f"Resolve answered unexpectedly: {e}")

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(timeout)
    return result
