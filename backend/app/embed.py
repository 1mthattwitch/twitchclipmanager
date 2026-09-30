"""Local text embeddings for meaning-based search (always offline after first download)."""
from __future__ import annotations

import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
_model = None
_failed = False
_lock = threading.Lock()


def _load():
    global _model, _failed
    if _model is not None or _failed:
        return _model
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME)
    except Exception:
        _failed = True
    return _model


def available() -> bool:
    with _lock:
        return _load() is not None


def embed(texts: list[str], query: bool = False) -> np.ndarray | None:
    with _lock:
        model = _load()
        if model is None:
            return None
        if query:
            texts = [f"Represent this sentence for searching relevant passages: {t}" for t in texts]
        vecs = np.array(list(model.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vecs / norms


def clip_document(clip: dict) -> str:
    """The text we embed for a clip: what an editor would describe it as."""
    a = clip.get("analysis") or {}
    parts = [
        clip.get("title") or "",
        clip.get("game_name") or "",
        clip.get("category") or "",
        clip.get("summary") or "",
        " ".join(clip.get("tags") or []),
        " ".join(a.get("search_phrases") or []),
        " ".join(m.get("description", "") for m in a.get("moments") or []),
        (clip.get("transcript_text") or "")[:1000],
    ]
    return "\n".join(p for p in parts if p)


def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
