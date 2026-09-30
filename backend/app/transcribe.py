"""Local speech-to-text with faster-whisper. Works offline once the model is cached."""
from __future__ import annotations

import os
import site
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

_model = None
_model_key: tuple | None = None
_lock = threading.Lock()
_load_error: str | None = None
_load_failed_at = 0.0
RETRY_LOAD_AFTER = 600


_dll_dirs_added = False
_cuda_ok: dict[tuple, bool] = {}

# Runs in a separate process: a broken CUDA/cuDNN install can crash the process outright
# instead of raising, so we never try the GPU in the app itself until this has passed.
_PROBE = """
import sys, numpy as np
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cuda", device_index=int(sys.argv[2]), compute_type="float16")
segs, _ = m.transcribe(np.random.default_rng(0).standard_normal(16000 * 2).astype("float32") * 0.1,
                       vad_filter=False, beam_size=1)
list(segs)
"""


def _add_windows_cuda_dlls() -> None:
    """pip's nvidia-cublas/cudnn wheels put their DLLs in site-packages; make them findable."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return
    _dll_dirs_added = True
    for base in map(Path, site.getsitepackages() + [site.getusersitepackages()]):
        for bin_dir in base.glob("nvidia/*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def cuda_works(model_name: str, gpu_index: int) -> bool:
    key = (model_name, gpu_index)
    if key not in _cuda_ok:
        try:
            r = subprocess.run([sys.executable, "-c", _PROBE, model_name, str(gpu_index)],
                               capture_output=True, text=True, timeout=900, env=os.environ.copy())
            _cuda_ok[key] = r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            _cuda_ok[key] = False
    return _cuda_ok[key]


def _load():
    global _model, _model_key
    s = config.get_settings()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    _add_windows_cuda_dlls()
    device = "cpu" if _cpu_only else s.whisper_device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if device == "cuda" and not cuda_works(s.whisper_model, s.whisper_gpu_index):
        device = "cpu"  # GPU libraries missing or broken: slower, but it works
    key = (s.whisper_model, device, s.whisper_gpu_index)
    if _model is None or _model_key != key:
        compute = "float16" if device == "cuda" else "int8"
        _model = WhisperModel(s.whisper_model, device=device, device_index=s.whisper_gpu_index,
                              compute_type=compute)
        _model_key = key
    return _model


def transcribe(video: Path) -> list[dict] | None:
    """Return [{start, end, text}] segments, or None if speech-to-text isn't available.

    None (unavailable) is different from [] (no speech): the AI is told which one it is.
    """
    global _load_error, _load_failed_at
    with _lock:
        if _load_error and time.time() - _load_failed_at < RETRY_LOAD_AFTER:
            return None
        try:
            model = _load()
        except Exception as e:  # model download failed, CUDA problem, ...
            _load_error, _load_failed_at = f"Speech-to-text unavailable: {e}", time.time()
            return None
        if model is None:
            _load_error, _load_failed_at = "faster-whisper is not installed", time.time()
            return None
        _load_error = None
        try:
            audio = decode_audio(video)
            if audio is None:
                return []  # the clip has no sound
            try:
                return _run(model, audio)
            except RuntimeError as e:
                # Typically missing CUDA/cuDNN libraries on Windows: fall back to the CPU.
                if "cud" not in str(e).lower() or _model_key and _model_key[1] == "cpu":
                    raise
                _force_cpu()
                return _run(_load(), audio)
        except Exception as e:
            # Never fail the whole clip over speech: it's analysed from its frames instead.
            _load_error = f"Speech-to-text failed: {str(e).splitlines()[0] if str(e) else type(e).__name__}"
            return None


def decode_audio(video: Path):
    """16 kHz mono float32 samples via FFmpeg (None if the clip has no audio).

    Done here rather than by faster-whisper, whose own decoder (PyAV) breaks
    whenever PyAV changes its API.
    """
    import numpy as np
    from .media import _require_ffmpeg
    r = subprocess.run(
        [_require_ffmpeg(), "-nostdin", "-loglevel", "error", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        if "does not contain any stream" in err or "matches no streams" in err or not err:
            return None
        raise RuntimeError(f"FFmpeg couldn't read the audio: {err.splitlines()[-1]}")
    audio = np.frombuffer(r.stdout, dtype=np.float32)
    return audio if audio.size else None


def _run(model, audio) -> list[dict]:
    segments, _info = model.transcribe(audio, vad_filter=True, beam_size=5)
    return [
        {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
        for seg in segments if seg.text.strip()
    ]


_cpu_only = False


def _force_cpu() -> None:
    global _cpu_only, _model
    _cpu_only, _model = True, None


def last_error() -> str | None:
    return _load_error


def device_in_use() -> str | None:
    return _model_key[1] if _model_key else None


def as_text(segments: list[dict] | None) -> str:
    return " ".join(s["text"] for s in segments or [])


def as_timed_text(segments: list[dict] | None) -> str:
    if segments is None:
        return "(transcript unavailable: judge from the frames only and say so if unsure)"
    if not segments:
        return "(no speech detected)"
    return "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in segments)
