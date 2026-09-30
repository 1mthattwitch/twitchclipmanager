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
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


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
    try:
        res = resolve  # noqa: F821 - provided by Resolve when run from the Scripts menu
    except NameError:
        res = app.GetResolve()  # noqa: F821
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
