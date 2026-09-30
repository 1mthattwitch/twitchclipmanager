"""Twitch Clip Manager -> DaVinci Resolve

Copy this file into Resolve's Utility scripts folder:
  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\
  macOS:   ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
Then run it from Workspace > Scripts > Clip Manager.

It pulls every clip you pressed "Queue for Resolve" on in the app and imports them
into the current project (bins, metadata and markers included).
"""
import json
import urllib.request

APP = "http://localhost:8765"


def fetch(path, data=None):
    req = urllib.request.Request(APP + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", "X-Clip-Manager": "1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def connect_resolve():
    """Resolve exposes itself differently depending on version and how the script was started."""
    g = globals()
    if g.get("resolve"):
        return g["resolve"]
    for name in ("bmd", "fusion", "app"):
        obj = g.get(name)
        if obj is None:
            continue
        for attr in ("scriptapp", "GetResolve"):
            fn = getattr(obj, attr, None)
            if fn:
                try:
                    r = fn("Resolve") if attr == "scriptapp" else fn()
                    if r:
                        return r
                except Exception:
                    pass
    try:
        import DaVinciResolveScript as dvr  # noqa: F401
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def main():
    try:
        source = fetch("/api/resolve/bridge.py")
    except Exception as e:
        print("Clip Manager isn't running at %s (%s). Start it first." % (APP, e))
        return
    bridge = {}
    exec(compile(source, "resolve_bridge.py", "exec"), bridge)

    queue = json.loads(fetch("/api/resolve/queue"))
    if not queue["items"]:
        print("Nothing queued. Press 'Queue for Resolve' on clips in the app first.")
        return
    res = connect_resolve()
    if res is None:
        print("Couldn't connect to Resolve. Run this from Workspace > Scripts inside DaVinci Resolve.")
        return
    groups = {}
    for it in queue["items"]:
        groups.setdefault(bool(it.get("append")), []).append(it)
    total = {"imported": 0, "already_there": 0, "errors": []}
    for append, items in groups.items():
        out = bridge["push_clips"](res, items, queue.get("bin_root", "Twitch Clips"), append)
        total["imported"] += out["imported"]
        total["already_there"] += out["already_there"]
        total["errors"] += out["errors"]
    fetch("/api/resolve/queue/ack", {"ids": [it["queue_id"] for it in queue["items"]]})
    print("Imported %d clip(s), %d already in the project." % (total["imported"], total["already_there"]))
    for err in total["errors"]:
        print("  ! " + err)


main()
