"""Local speech-to-text with faster-whisper. Works offline once the model is cached."""
from __future__ import annotations

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


def _load():
    global _model, _model_key
    s = config.get_settings()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    device = "cpu" if _cpu_only else s.whisper_device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
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
            return _run(model, video)
        except RuntimeError as e:
            # Typically missing CUDA/cuDNN libraries on Windows: fall back to the CPU.
            if "cud" not in str(e).lower() or _model_key and _model_key[1] == "cpu":
                raise
            _force_cpu()
            return _run(_load(), video)


def _run(model, video: Path) -> list[dict]:
    segments, _info = model.transcribe(str(video), vad_filter=True, beam_size=5)
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


def as_text(segments: list[dict] | None) -> str:
    return " ".join(s["text"] for s in segments or [])


def as_timed_text(segments: list[dict] | None) -> str:
    if segments is None:
        return "(transcript unavailable: judge from the frames only and say so if unsure)"
    if not segments:
        return "(no speech detected)"
    return "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in segments)
