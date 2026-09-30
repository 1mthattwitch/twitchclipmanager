"""Downloading clips and pulling still frames out of them."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config


class MediaError(RuntimeError):
    pass


def _slug(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_]+", "_", text)
    return (text[:limit] or "clip").strip("_")


def clip_path(clip: dict, streamer_login: str) -> Path:
    lib = Path(config.get_settings().library_dir)
    date = (clip.get("created_at") or "")[:10]
    return lib / streamer_login / f"{date}_{_slug(clip['title'])}_{clip['id'][-8:]}.mp4"


def frames_dir(clip_id: str) -> Path:
    return config.DATA_DIR / "frames" / re.sub(r"[^\w-]", "_", clip_id)


def download(clip: dict, streamer_login: str) -> Path:
    target = clip_path(clip, streamer_login)
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover
        raise MediaError("yt-dlp is not installed (pip install yt-dlp)") from e
    opts = {
        "outtmpl": str(target),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([clip["url"]])
    except Exception as e:
        raise MediaError(f"Download failed: {e}") from e
    if not target.exists():
        raise MediaError("Download finished but no file was written.")
    return target


def ffmpeg_exe() -> str | None:
    """System FFmpeg if installed, otherwise the copy bundled with imageio-ffmpeg."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _require_ffmpeg() -> str:
    path = ffmpeg_exe()
    if not path:
        raise MediaError("FFmpeg was not found. Run: pip install imageio-ffmpeg (or install FFmpeg).")
    return path


def probe_duration(video: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
            capture_output=True, text=True, check=False,
        )
        try:
            return float(json.loads(out.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    # No ffprobe (e.g. bundled FFmpeg): read the duration from ffmpeg's banner.
    out = subprocess.run([_require_ffmpeg(), "-hide_banner", "-i", str(video)],
                         capture_output=True, text=True, check=False)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr)
    if not m:
        return 0.0
    h, mi, sec = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(sec)


def frame_times(duration: float, count: int) -> list[float]:
    """Evenly spaced sample points, avoiding the very first/last frame."""
    if duration <= 0 or count <= 0:
        return [0.0]
    step = duration / count
    return [round(step * (i + 0.5), 2) for i in range(count)]


def extract_frames(video: Path, clip_id: str, count: int, fallback_duration: float = 0) -> list[dict]:
    ffmpeg = _require_ffmpeg()
    duration = probe_duration(video) or fallback_duration
    out_dir = frames_dir(clip_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(frame_times(duration, count)):
        target = out_dir / f"f{i:02d}.jpg"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
             "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", str(target)],
            check=False, capture_output=True,
        )
        if target.exists():
            frames.append({"path": str(target), "t": t})
    if not frames:
        raise MediaError("Could not extract any frames from the clip.")
    return frames
