"""Command-line setup steps, used by install.bat (and handy by hand).

    python -m app.setup_cli ollama [--yes]   pick the model folder, set OLLAMA_MODELS, start Ollama,
                                             download the model only if it isn't there already
    python -m app.setup_cli resolve          install the Resolve script if Resolve is installed
"""
from __future__ import annotations

import argparse
import sys
import threading
import time

from .services import ollama_manager, resolve_setup


def _ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return True


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def setup_ollama(assume_yes: bool = False) -> int:
    p = ollama_manager.plan()
    if not p["installed"]:
        print("Ollama isn't installed, so the offline AI is skipped. Install it from https://ollama.com/download")
        return 3
    print("Looking for your existing Ollama models...")
    for st in p["stores"]:
        vision = [m["name"] for m in st["models"] if m["vision"] and m["complete"]]
        print(f"  {st['path']}: {len(st['models'])} model(s), {_gb(st['size'])}"
              + (f", vision: {', '.join(vision)}" if vision else ""))
    for note in p["notes"]:
        print(f"  ({note})")
    c = p["choice"]
    print(c["reason"])
    for line in ollama_manager.apply_choice(c["store"], c["model"], system_wide=True):
        print("  " + line)

    if p["running"] and p["mismatch"]:
        if _ask(f"Ollama is running with a different model folder. Restart it to use {c['store']}?", assume_yes):
            print("Restarting Ollama...")
            if not ollama_manager.restart(c["store"]):
                print("Ollama didn't come back. Start it from the Start menu and run install.bat again.")
                return 1
    elif not p["running"]:
        print("Starting Ollama...")
        if not ollama_manager.start(c["store"]):
            print("Ollama didn't start. Open it from the Start menu, then run install.bat again.")
            return 1

    if not c["needs_download"] and c["model"]:
        print(f"Offline AI is ready: {c['model']} (no download needed).")
        return 0
    print(f"Downloading {c['model']} (about 6 GB; it resumes if interrupted)...")
    t = threading.Thread(target=ollama_manager.pull, args=(c["model"],), daemon=True)
    t.start()
    last = None
    while t.is_alive():
        st = ollama_manager.pull_state()
        line = f"  {st['percent']:5.1f}%  {st['status']}"
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(2)
    st = ollama_manager.pull_state()
    if st["error"]:
        print(f"The download failed: {st['error']}. Run install.bat again to resume it.")
        return 1
    print(f"Offline AI is ready: {c['model']}.")
    return 0


def setup_resolve() -> int:
    if not resolve_setup.resolve_installed():
        print("DaVinci Resolve isn't installed here; skipping the Resolve script.")
        return 0
    s = resolve_setup.install_script()
    print(f"Installed the Clip Manager script for Resolve: {s['script_path']}")
    print("In Resolve: Preferences > System > General > External scripting using: Local")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.setup_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("ollama")
    o.add_argument("--yes", action="store_true", help="don't ask before restarting Ollama")
    sub.add_parser("resolve")
    args = ap.parse_args(argv)
    if args.cmd == "ollama":
        return setup_ollama(args.yes)
    return setup_resolve()


if __name__ == "__main__":
    sys.exit(main())
