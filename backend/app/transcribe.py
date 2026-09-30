"""Local speech-to-text with faster-whisper. Works offline once the model is cached."""
from __future__ import annotations

import threading
from pathlib import Path

from . import config

_model = None
_model_key: tuple | None = None
_lock = threading.Lock()


def _load():
    global _model, _model_key
    s = config.get_settings()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    device = s.whisper_device
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


def transcribe(video: Path) -> list[dict]:
    """Return [{start, end, text}] segments. Empty list if Whisper isn't installed."""
    with _lock:
        model = _load()
        if model is None:
            return []
        segments, _info = model.transcribe(str(video), vad_filter=True, beam_size=5)
        return [
            {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
            for seg in segments if seg.text.strip()
        ]


def as_text(segments: list[dict]) -> str:
    return " ".join(s["text"] for s in segments)


def as_timed_text(segments: list[dict]) -> str:
    if not segments:
        return "(no speech detected)"
    return "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in segments)
