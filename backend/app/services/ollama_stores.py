"""Find existing Ollama model folders ("stores") and what's in them.

A store is a folder with Ollama's `blobs` and `manifests` subfolders. Users often keep
them on other drives (e.g. L:\\.DoNotTouch\\models\\.ollama), sometimes one level
deeper (".ollama\\models"). Everything here only reads; the app never deletes or
moves anything inside a store.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config

DEFAULT_REGISTRY = "registry.ollama.ai"
DEFAULT_NAMESPACE = "library"
# Families that can look at images. A manifest with a "projector" layer is also vision-capable.
VISION_FAMILIES = ("qwen2.5vl", "qwen2.5-vl", "qwen3-vl", "qwen3vl", "llava", "llama3.2-vision",
                   "minicpm-v", "gemma3", "moondream", "bakllava", "granite3.2-vision", "mistral-small3.1")
PROJECTOR_MEDIA = "application/vnd.ollama.image.projector"
MAX_DEPTH = 2


@dataclass
class ModelInfo:
    name: str               # e.g. "qwen2.5vl:7b"
    size: int               # bytes, sum of layers
    vision: bool
    complete: bool          # all blobs present
    missing_blobs: int = 0


@dataclass
class Store:
    path: str
    root: str               # the configured folder it was found under
    models: list[ModelInfo] = field(default_factory=list)

    def has(self, name: str) -> bool:
        want = normalize(name)
        return any(normalize(m.name) == want and m.complete for m in self.models)

    def vision_models(self) -> list[ModelInfo]:
        return [m for m in self.models if m.vision and m.complete]

    def to_dict(self) -> dict:
        return {"path": self.path, "root": self.root, "models": [asdict(m) for m in self.models],
                "size": sum(m.size for m in self.models)}


def normalize(name: str) -> str:
    """'qwen2.5vl' -> 'qwen2.5vl:latest'; strips the default registry/namespace."""
    name = name.strip()
    for prefix in (f"{DEFAULT_REGISTRY}/{DEFAULT_NAMESPACE}/", f"{DEFAULT_NAMESPACE}/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if ":" not in name.rsplit("/", 1)[-1]:
        name += ":latest"
    return name.lower()


def is_store(path: Path) -> bool:
    try:
        return (path / "blobs").is_dir() and (path / "manifests").is_dir()
    except OSError:
        return False


def find_stores_under(root: Path, depth: int = MAX_DEPTH) -> list[Path]:
    """The root itself if it's a store, else stores up to `depth` folders below it."""
    try:
        if not root.is_dir():
            return []
    except OSError:  # e.g. a disconnected drive
        return []
    if is_store(root):
        return [root]
    if depth == 0:
        return []
    found: list[Path] = []
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    # Ollama's own layout is <home>/.ollama/models; check the usual names first.
    children.sort(key=lambda p: (p.name.lower() not in ("models", ".ollama", "ollama"), p.name.lower()))
    for child in children:
        if child.name.lower() in ("blobs", "manifests"):
            continue
        found += find_stores_under(child, depth - 1)
    return found


def _model_name(manifest_path: Path, manifests_dir: Path) -> str | None:
    parts = manifest_path.relative_to(manifests_dir).parts
    if len(parts) < 3:
        return None
    *prefix, model, tag = parts
    registry = prefix[0] if prefix else DEFAULT_REGISTRY
    namespace = "/".join(prefix[1:]) if len(prefix) > 1 else DEFAULT_NAMESPACE
    base = model
    if namespace != DEFAULT_NAMESPACE:
        base = f"{namespace}/{model}"
    if registry != DEFAULT_REGISTRY:
        base = f"{registry}/{base}"
    return f"{base}:{tag}"


def _blob_path(blobs: Path, digest: str) -> Path:
    return blobs / digest.replace(":", "-")


def inventory(store: Path) -> list[ModelInfo]:
    manifests_dir, blobs = store / "manifests", store / "blobs"
    models: list[ModelInfo] = []
    try:
        files = [p for p in manifests_dir.rglob("*") if p.is_file()]
    except OSError:
        return models
    for mf in sorted(files):
        name = _model_name(mf, manifests_dir)
        if not name:
            continue
        try:
            data = json.loads(mf.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        layers = [data.get("config") or {}] + list(data.get("layers") or [])
        layers = [l for l in layers if isinstance(l, dict) and l.get("digest")]
        missing = sum(1 for l in layers if not _blob_path(blobs, l["digest"]).exists())
        size = sum(int(l.get("size") or 0) for l in layers)
        family = name.rsplit("/", 1)[-1].split(":")[0].lower()
        vision = any(l.get("mediaType") == PROJECTOR_MEDIA for l in layers) or family.startswith(VISION_FAMILIES)
        models.append(ModelInfo(name=name, size=size, vision=vision, complete=missing == 0,
                                missing_blobs=missing))
    return models


def candidate_roots() -> list[str]:
    """Configured folders first (user's order), then Ollama's env var and default folder."""
    s = config.get_settings()
    roots = list(s.ollama_model_dirs)
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        roots.append(env)
    roots.append(str(Path.home() / ".ollama" / "models"))
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(os.path.normpath(r)) if r else ""
        if r and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def discover(roots: list[str] | None = None) -> tuple[list[Store], list[str]]:
    """All stores found, in preference order, plus notes about folders that couldn't be used."""
    stores: list[Store] = []
    notes: list[str] = []
    seen: set[str] = set()
    for root in roots if roots is not None else candidate_roots():
        found = find_stores_under(Path(root))
        if not found:
            drive = Path(root).drive
            if drive and not Path(drive + os.sep).exists():
                notes.append(f"{root}: drive {drive} isn't connected")
            elif not Path(root).exists():
                notes.append(f"{root}: folder not found")
            else:
                notes.append(f"{root}: no Ollama models inside")
        for path in found:
            key = os.path.normcase(str(path.resolve()))
            if key in seen:
                continue
            seen.add(key)
            stores.append(Store(path=str(path), root=root, models=inventory(path)))
    return stores, notes


@dataclass
class Choice:
    store: str | None       # folder Ollama should use
    model: str              # model to use
    needs_download: bool    # model must be pulled into `store`
    reason: str


def choose(stores: list[Store], wanted_model: str, fallback_dir: str | None = None) -> Choice:
    """Pick the folder and model, in the user's preference order.

    1. First store that already has the wanted model.
    2. First store with any complete vision model (switch to it).
    3. First store at all, downloading the wanted model into it.
    4. No store found: `fallback_dir` (or Ollama's default) and download.
    """
    for st in stores:
        if st.has(wanted_model):
            return Choice(st.path, wanted_model, False, f"{wanted_model} is already in {st.path}")
    for st in stores:
        vision = st.vision_models()
        if vision:
            best = max(vision, key=lambda m: m.size)
            return Choice(st.path, best.name, False,
                          f"{wanted_model} isn't downloaded, but {best.name} (a vision model) is in {st.path}")
    if stores:
        return Choice(stores[0].path, wanted_model, True,
                      f"No vision model found; {wanted_model} will be downloaded into {stores[0].path}")
    return Choice(fallback_dir, wanted_model, True,
                  f"No existing model folders found; {wanted_model} will be downloaded"
                  + (f" into {fallback_dir}" if fallback_dir else " into Ollama's default folder"))


def store_model_names(path: str) -> set[str]:
    return {normalize(m.name) for m in inventory(Path(path)) if m.complete}
