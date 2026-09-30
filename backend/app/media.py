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


def temp_dir() -> Path:
    """Where a clip's video sits while the AI watches it. Emptied on every start."""
    return config.DATA_DIR / "watching"


def clear_temp() -> int:
    """Delete videos left behind by a crash or power cut. Returns how many."""
    n = 0
    if temp_dir().exists():
        for f in temp_dir().iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
                n += 1
    return n


def frames_dir(clip_id: str) -> Path:
    return config.DATA_DIR / "frames" / re.sub(r"[^\w-]", "_", clip_id)


class ClipGone(MediaError):
    """The clip was deleted on Twitch: a per-clip problem, not a broken setup."""


# Twitch's own website uses this public Client-ID and query for clip playback.
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
CLIP_TOKEN_QUERY = {
    "operationName": "VideoAccessToken_Clip",
    "extensions": {"persistedQuery": {"version": 1,
                                      "sha256Hash": "36b89d2507fce29e5ca551df756d27c1cfe079e2609642b4390aa4c35796eb11"}},
}
_upgrade_started = False


def _first_line(e: Exception) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return re.sub(r"^ERROR:\s*", "", text)[:300]


def _download_ytdlp(clip: dict, target: Path) -> None:
    import yt_dlp
    opts = {
        "outtmpl": {"default": str(target).replace("%", "%%")},
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "logger": _QuietLogger(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([clip["url"]])


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _download_direct(clip: dict, target: Path, http=None) -> None:
    """Fetch the clip the way Twitch's website does: playback token + signed MP4 URL."""
    import httpx
    from urllib.parse import urlencode
    if http is None:
        with httpx.Client(timeout=60, follow_redirects=True) as own:
            return _download_direct(clip, target, http=own)
    client = http
    body = [dict(CLIP_TOKEN_QUERY, variables={"slug": clip["id"]})]
    r = client.post("https://gql.twitch.tv/gql", json=body, headers={"Client-ID": TWITCH_WEB_CLIENT_ID})
    if r.status_code != 200:
        raise MediaError(f"Twitch answered {r.status_code}")
    data = r.json()
    data = data[0] if isinstance(data, list) else data
    if data.get("errors"):
        raise MediaError(f"Twitch said: {data['errors'][0].get('message', 'error')}")
    info = (data.get("data") or {}).get("clip")
    if not info:
        raise ClipGone("This clip was deleted or made private on Twitch")
    token = info.get("playbackAccessToken") or {}
    qualities = [q for q in info.get("videoQualities") or [] if q.get("sourceURL")]
    if not qualities or not token.get("signature"):
        raise MediaError("Twitch didn't return a playable video for this clip")
    best = max(qualities, key=lambda q: (int(re.sub(r"\D", "", str(q.get("quality") or 0)) or 0),
                                         float(q.get("frameRate") or 0)))
    sep = "&" if "?" in best["sourceURL"] else "?"
    url = best["sourceURL"] + sep + urlencode({"sig": token["signature"], "token": token["value"]})
    part = target.with_suffix(".mp4.part")
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise MediaError(f"The video server answered {resp.status_code}")
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if part.stat().st_size < 1024:
        part.unlink(missing_ok=True)
        raise MediaError("The downloaded video was empty")
    part.replace(target)


def _upgrade_ytdlp_in_background() -> None:
    """Twitch changes break old yt-dlp versions; fetch the newest one for next time."""
    global _upgrade_started
    if _upgrade_started:
        return
    _upgrade_started = True
    import sys
    import threading

    def run():
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"],
                       capture_output=True, timeout=600)
    threading.Thread(target=run, daemon=True).start()


def download(clip: dict, streamer_login: str, http=None, temp: bool = False) -> Path:
    """yt-dlp first; if that fails, Twitch's own playback API directly.

    temp=True puts the video in the watching folder (deleted after analysis)
    instead of your clip library.
    """
    safe_id = re.sub(r"[^\w-]", "_", clip["id"])
    target = temp_dir() / f"{safe_id}.mp4" if temp else clip_path(clip, streamer_login)
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    first = None
    try:
        _download_ytdlp(clip, target)
    except ImportError:
        first = "yt-dlp is not installed"
    except Exception as e:  # yt-dlp raises many types
        first = _first_line(e)
        _upgrade_ytdlp_in_background()
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        _download_direct(clip, target, http=http)
    except ClipGone:
        raise
    except Exception as e:
        Path(str(target) + ".part").unlink(missing_ok=True)  # yt-dlp's leftover
        raise MediaError(f"Download failed. yt-dlp: {first or 'no file written'}. Direct: {_first_line(e)}") from e
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
