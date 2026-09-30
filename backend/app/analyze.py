"""The per-clip pipeline: download -> frames -> transcript -> describe -> corroborate -> index."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import config, db, embed, media, transcribe
from .ai import prompts
from .ai.base import AIError, Provider, get_provider

Progress = Callable[[float, str], None]


def get_clip(clip_id: str) -> dict | None:
    return db.query_one(
        """SELECT c.*, s.login AS streamer_login, s.display_name AS streamer_name
           FROM clips c JOIN streamers s ON s.id = c.streamer_id WHERE c.id = ?""",
        [clip_id],
    )


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, type(default)(v)))
    except (TypeError, ValueError):
        return default


def normalize_analysis(raw: dict, duration: float) -> dict:
    """Make model output safe to store, whatever the model actually sent."""
    dur = float(duration or 0) or 60.0
    cats = set(prompts.CATEGORIES)
    category = raw.get("category") if raw.get("category") in cats else "Other"
    tags: list[str] = []
    for t in raw.get("tags") or []:
        t = str(t).strip().lower().lstrip("#")
        if t and t not in tags:
            tags.append(t)
    moments = []
    for m in raw.get("moments") or []:
        if isinstance(m, dict) and m.get("description"):
            moments.append({"t": _clamp(m.get("t"), 0.0, dur, 0.0), "description": str(m["description"])})
    moments.sort(key=lambda m: m["t"])
    best_in = _clamp(raw.get("best_in"), 0.0, dur, 0.0)
    best_out = _clamp(raw.get("best_out"), 0.0, dur, dur)
    if best_out <= best_in:
        best_in, best_out = 0.0, dur
    return {
        "summary": str(raw.get("summary") or "").strip(),
        "category": category,
        "secondary_categories": [c for c in raw.get("secondary_categories") or [] if c in cats and c != category],
        "tags": tags[:25],
        "moments": moments[:20],
        "on_screen": [str(x) for x in raw.get("on_screen") or []][:15],
        "mood": str(raw.get("mood") or "").strip().lower(),
        "energy": _clamp(raw.get("energy"), 1, 5, 3),
        "profanity": bool(raw.get("profanity")),
        "best_in": best_in,
        "best_out": best_out,
        "search_phrases": [str(x) for x in raw.get("search_phrases") or []][:10],
        "confidence": _clamp(raw.get("confidence"), 0.0, 1.0, 0.5),
    }


def apply_verification(analysis: dict, verification: dict, threshold: float) -> tuple[dict, float, bool]:
    """Fold a fact-check back into the analysis. Returns (analysis, confidence, needs_review)."""
    analysis = dict(analysis)
    remove = {str(t).strip().lower() for t in verification.get("remove_tags") or []}
    if remove:
        analysis["tags"] = [t for t in analysis["tags"] if t not in remove]
    corrected = (verification.get("corrected_summary") or "").strip()
    if corrected:
        analysis["summary"] = corrected
    checks = verification.get("checks") or []
    refuted = sum(1 for c in checks if c.get("supported") == "no")
    unsure = sum(1 for c in checks if c.get("supported") == "unsure")
    ratio = 1.0
    if checks:
        ratio = 1.0 - (refuted + 0.5 * unsure) / len(checks)
    verify_conf = _clamp(verification.get("confidence"), 0.0, 1.0, 0.5)
    confidence = round(min(analysis["confidence"], verify_conf) * (0.5 + 0.5 * ratio), 3)
    needs_review = confidence < threshold or refuted > 0
    return analysis, confidence, needs_review


def _evidence(clip: dict) -> tuple[str, list[Path]]:
    frames = clip.get("frames") or []
    transcript = transcribe.as_timed_text(clip.get("transcript") or [])
    context = prompts.clip_context(clip, frames, transcript)
    images = [Path(f["path"]) for f in frames if Path(f["path"]).exists()]
    return context, images


def _save_checks(clip_id: str, verification: dict, kind: str, provider_label: str) -> None:
    for c in verification.get("checks") or []:
        supported = {"yes": 1, "no": 0}.get(c.get("supported"))
        db.execute(
            "INSERT INTO qa (clip_id, kind, question, answer, supported, provider, created_at) VALUES (?,?,?,?,?,?,?)",
            [clip_id, kind, c.get("question") or c.get("claim") or "", c.get("answer") or "",
             supported, provider_label, db.now()],
        )


def reembed(clip_id: str) -> None:
    clip = get_clip(clip_id)
    if not clip:
        return
    db.reindex_clip(clip_id)
    vecs = embed.embed([embed.clip_document(clip)])
    if vecs is not None:
        db.execute("UPDATE clips SET embedding = ? WHERE id = ?", [embed.to_blob(vecs[0]), clip_id])
        from . import search
        search.invalidate_cache()


def _label(provider: Provider) -> str:
    return getattr(provider, "label", provider.name)


def verify_clip(clip_id: str, provider: Provider, kind: str = "verify") -> dict:
    clip = get_clip(clip_id)
    if not clip or not clip.get("analysis"):
        raise AIError("Clip hasn't been analysed yet.")
    context, images = _evidence(clip)
    raw = provider.generate_json(prompts.VERIFY_SYSTEM, prompts.verify_prompt(context, clip["analysis"]),
                                 images, prompts.VERIFY_SCHEMA)
    threshold = config.get_settings().review_threshold
    analysis, confidence, needs_review = apply_verification(clip["analysis"], raw, threshold)
    db.execute("DELETE FROM qa WHERE clip_id = ? AND kind = ?", [clip_id, kind])
    _save_checks(clip_id, raw, kind, _label(provider))
    fields = dict(verification=raw, confidence=confidence, needs_review=int(needs_review))
    if not clip.get("corrected"):
        fields.update(analysis=analysis, summary=analysis["summary"], tags=analysis["tags"])
    db.update("clips", "id", clip_id, **fields)
    reembed(clip_id)
    return raw


def run_pipeline(clip_id: str, provider_name: str | None = None, progress: Progress | None = None) -> None:
    progress = progress or (lambda p, m: None)
    s = config.get_settings()
    clip = get_clip(clip_id)
    if not clip:
        raise ValueError(f"Unknown clip {clip_id}")

    def stage(status: str, pct: float, msg: str):
        db.update("clips", "id", clip_id, status=status, error=None)
        progress(pct, msg)

    provider = get_provider(provider_name)
    ok, why = provider.available()
    if not ok:
        raise AIError(why)

    # 1. Download
    stage("downloading", 0.05, "Downloading clip")
    path = Path(clip["file_path"]) if clip.get("file_path") else None
    if not path or not path.exists():
        path = media.download(clip, clip["streamer_login"])
        db.update("clips", "id", clip_id, file_path=str(path))

    # 2. Frames + transcript
    stage("transcribing", 0.25, "Extracting frames")
    frames = media.extract_frames(path, clip_id, s.frames_per_clip, clip.get("duration") or 0)
    progress(0.35, "Transcribing speech")
    segments = transcribe.transcribe(path)
    db.update("clips", "id", clip_id, frames=frames, transcript=segments,
              transcript_text=transcribe.as_text(segments))

    # 3. Describe
    stage("analyzing", 0.55, f"Watching with {_label(provider)}")
    clip = get_clip(clip_id)
    context, images = _evidence(clip)
    raw = provider.generate_json(prompts.DESCRIBE_SYSTEM, prompts.describe_prompt(context), images,
                                 prompts.ANALYSIS_SCHEMA)
    analysis = normalize_analysis(raw, clip.get("duration") or 0)
    db.update("clips", "id", clip_id, analysis=analysis, summary=analysis["summary"],
              category=analysis["category"], tags=analysis["tags"], mood=analysis["mood"],
              energy=analysis["energy"], confidence=analysis["confidence"], corrected=0,
              provider=_label(provider), analyzed_at=db.now())

    # 4. Corroborate
    progress(0.8, "Double-checking the description")
    verify_clip(clip_id, provider)

    # 5. Optional cross-check of shaky local results with Claude
    clip = get_clip(clip_id)
    if (s.claude_cross_check and provider.name == "local" and clip["needs_review"]
            and s.anthropic_api_key):
        progress(0.9, "Cross-checking with Claude")
        try:
            verify_clip(clip_id, get_provider("claude"), kind="crosscheck")
        except AIError:
            pass  # offline or no credit: keep the local result

    db.update("clips", "id", clip_id, status="done")
    progress(1.0, "Done")


def ask(clip_id: str, question: str, provider_name: str | None = None) -> dict:
    clip = get_clip(clip_id)
    if not clip:
        raise ValueError("Unknown clip")
    if not clip.get("frames"):
        raise AIError("Analyse this clip first so the AI has something to look at.")
    provider = get_provider(provider_name)
    history = db.query("SELECT question, answer FROM qa WHERE clip_id = ? AND kind = 'user' ORDER BY id", [clip_id])
    context, images = _evidence(clip)
    answer = provider.generate_text(prompts.ASK_SYSTEM,
                                    prompts.ask_prompt(context, clip.get("analysis"), history, question),
                                    images)
    cur = db.execute(
        "INSERT INTO qa (clip_id, kind, question, answer, provider, created_at) VALUES (?,?,?,?,?,?)",
        [clip_id, "user", question, answer, _label(provider), db.now()],
    )
    return db.query_one("SELECT * FROM qa WHERE id = ?", [cur.lastrowid])


def correct(clip_id: str, summary: str | None = None, tags: list[str] | None = None,
            category: str | None = None) -> dict:
    """Editor overrides. Saved as ground truth and never overwritten by re-checks."""
    clip = get_clip(clip_id)
    if not clip:
        raise ValueError("Unknown clip")
    analysis = dict(clip.get("analysis") or {})
    fields: dict = {"corrected": 1, "needs_review": 0, "confidence": 1.0}
    if summary is not None:
        analysis["summary"] = fields["summary"] = summary.strip()
    if tags is not None:
        clean = list(dict.fromkeys(t.strip().lower() for t in tags if t.strip()))
        analysis["tags"] = fields["tags"] = clean
    if category is not None and category in prompts.CATEGORIES:
        analysis["category"] = fields["category"] = category
    fields["analysis"] = analysis
    db.update("clips", "id", clip_id, **fields)
    reembed(clip_id)
    return get_clip(clip_id)
