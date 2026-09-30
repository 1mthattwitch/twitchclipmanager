"""DaVinci Resolve Studio integration.

Standard library only: the in-Resolve script (resolve/Clip Manager.py) downloads
this file from the running app and executes it inside Resolve, so both paths use
exactly the same import logic.
"""
from __future__ import annotations

import os
import sys

MARKER_COLORS = {
    "Funny": "Yellow", "Fail": "Red", "Clutch / Highlight": "Green", "Rage": "Red",
    "Jumpscare / Scary": "Purple", "Wholesome": "Pink", "Chat / Donation Reaction": "Cyan",
    "IRL": "Sand", "Music": "Lavender", "Collab / Guest": "Blue", "Drama / Serious": "Cocoa",
}


class ResolveError(RuntimeError):
    pass


def _default_paths() -> tuple[str, str]:
    if sys.platform.startswith("win"):
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return (os.path.join(program_data, r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")
    if sys.platform == "darwin":
        return ("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
                "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
    return ("/opt/resolve/Developer/Scripting/Modules", "/opt/resolve/libs/Fusion/fusionscript.so")


def get_resolve():
    """Connect to a running Resolve Studio (external scripting must be set to Local)."""
    modules, lib = _default_paths()
    os.environ.setdefault("RESOLVE_SCRIPT_API", os.path.dirname(modules))
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    if modules not in sys.path:
        sys.path.append(modules)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise ResolveError("Couldn't find DaVinci Resolve's scripting module. Is Resolve Studio installed?") from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError(
            "Resolve isn't reachable. Open Resolve Studio and set Preferences > System > General > "
            "External scripting using: Local.")
    return resolve


def _ensure_folder(media_pool, parent, name: str):
    for folder in parent.GetSubFolderList() or []:
        if folder.GetName() == name:
            return folder
    folder = media_pool.AddSubFolder(parent, name)
    if folder is None:
        raise ResolveError(f"Couldn't create bin '{name}'")
    return folder


def _find_existing(folder, path: str):
    norm = os.path.normcase(os.path.abspath(path))
    for item in folder.GetClipList() or []:
        fp = item.GetClipProperty("File Path") or ""
        if fp and os.path.normcase(os.path.abspath(fp)) == norm:
            return item
    return None


def _fps(item, project) -> float:
    for value in (item.GetClipProperty("FPS"), project.GetSetting("timelineFrameRate")):
        try:
            fps = float(value)
            if fps > 0:
                return fps
        except (TypeError, ValueError):
            pass
    return 30.0


def push_clips(resolve, items: list[dict], bin_root: str = "Twitch Clips", append: bool = False) -> dict:
    """Import clips into the current project, organised into bins, with metadata and markers.

    Each item: path, title, summary, tags, category, streamer, moments[{t,description}], best_in, best_out.
    """
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise ResolveError("Open a project in Resolve first.")
    media_pool = project.GetMediaPool()
    root_bin = _ensure_folder(media_pool, media_pool.GetRootFolder(), bin_root)
    imported, skipped, errors = 0, 0, []
    timeline = None

    for it in items:
        path = it.get("path")
        if not path or not os.path.exists(path):
            errors.append(f"{it.get('title')}: file not downloaded")
            continue
        folder = _ensure_folder(media_pool, root_bin, it.get("streamer") or "Unknown")
        folder = _ensure_folder(media_pool, folder, it.get("category") or "Unsorted")
        item = _find_existing(folder, path)
        if item is None:
            media_pool.SetCurrentFolder(folder)
            result = media_pool.ImportMedia([path]) or []
            if not result:
                errors.append(f"{it.get('title')}: Resolve refused the file")
                continue
            item = result[0]
            imported += 1
        else:
            skipped += 1

        item.SetMetadata({
            "Description": it.get("title") or "",
            "Comments": it.get("summary") or "",
            "Keywords": ", ".join(it.get("tags") or []),
        })
        fps = _fps(item, project)
        color = MARKER_COLORS.get(it.get("category") or "", "Blue")
        for m in it.get("moments") or []:
            frame = int(round(float(m.get("t", 0)) * fps))
            text = str(m.get("description", ""))
            # AddMarker fails harmlessly if a marker already sits on that frame.
            item.AddMarker(frame, color, text[:60], text, 1)

        if append:
            if timeline is None:
                timeline = project.GetCurrentTimeline()
                if timeline is None:
                    timeline = media_pool.CreateEmptyTimeline(f"{bin_root} selects")
                    project.SetCurrentTimeline(timeline)
            start = int(float(it.get("best_in") or 0) * fps)
            end = int(float(it.get("best_out") or 0) * fps) - 1
            entry = {"mediaPoolItem": item}
            if end > start:
                entry.update(startFrame=start, endFrame=end)
            media_pool.AppendToTimeline([entry])

    return {"imported": imported, "already_there": skipped, "errors": errors}
