"""FastAPI app: JSON API under /api, media under /media, the built UI at /."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import analyze, config, db, embed, media, resolve_bridge, search, transcribe, worker
from .ai.base import AIError, get_provider
from .ai.prompts import CATEGORIES
from .twitch import TwitchClient, TwitchError

FRONTEND_DIST = config.ROOT_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    if os.environ.get("TCM_NO_WORKER") != "1":
        worker.start()
        # Load (or first-time download) the search model without blocking startup.
        threading.Thread(target=embed.available, daemon=True).start()
    yield
    worker.stop()


app = FastAPI(title="Twitch Clip Manager", lifespan=lifespan)


def _clip_or_404(clip_id: str) -> dict:
    clip = analyze.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return clip


def _bad(e: Exception):
    raise HTTPException(400, str(e))


# ---------- settings & status ----------

@app.get("/api/settings")
def get_settings():
    return {"settings": config.public_settings(), "categories": CATEGORIES}


@app.put("/api/settings")
def put_settings(patch: dict = Body(...)):
    config.save_settings(patch)
    return {"settings": config.public_settings()}


@app.get("/api/status")
def status():
    s = config.get_settings()
    out = {
        "twitch": bool(s.twitch_client_id and s.twitch_client_secret),
        "ffmpeg": bool(media.ffmpeg_exe()),
        "mode": s.ai_mode,
    }
    try:
        import faster_whisper  # noqa: F401
        out["whisper"] = True
    except ImportError:
        out["whisper"] = False
    out["embeddings"] = embed.available()
    out["whisper_error"] = transcribe.last_error()
    for name in ("local", "claude"):
        try:
            ok, msg = get_provider(name).available()
        except AIError as e:
            ok, msg = False, str(e)
        out[name] = {"ok": ok, "message": msg}
    return out


# ---------- streamers ----------

@app.get("/api/streamers")
def list_streamers():
    return db.query(
        """SELECT s.*, COUNT(c.id) AS clip_count,
                  SUM(CASE WHEN c.status='done' THEN 1 ELSE 0 END) AS analyzed_count
           FROM streamers s LEFT JOIN clips c ON c.streamer_id = s.id
           GROUP BY s.id ORDER BY s.display_name COLLATE NOCASE""")


@app.post("/api/streamers")
def add_streamer(body: dict = Body(...)):
    login = (body.get("login") or "").strip()
    if not login:
        _bad(ValueError("Type a streamer's name"))
    try:
        user = TwitchClient().get_user(login)
    except TwitchError as e:
        _bad(e)
    existing = db.query_one("SELECT * FROM streamers WHERE twitch_id = ?", [user["id"]])
    if not existing:
        db.execute(
            "INSERT INTO streamers (twitch_id, login, display_name, profile_image_url, created_at) VALUES (?,?,?,?,?)",
            [user["id"], user["login"], user["display_name"], user.get("profile_image_url"), db.now()])
        existing = db.query_one("SELECT * FROM streamers WHERE twitch_id = ?", [user["id"]])
    since = body.get("since_days")
    worker.enqueue_sync(existing["id"], int(since) if since else None)
    return existing


@app.post("/api/streamers/{streamer_id}/sync")
def sync_streamer(streamer_id: int, body: dict = Body(default={})):
    since = body.get("since_days")
    return {"job_id": worker.enqueue_sync(streamer_id, int(since) if since else None, bool(body.get("full")))}


@app.delete("/api/streamers/{streamer_id}")
def delete_streamer(streamer_id: int):
    ids = [r["id"] for r in db.query("SELECT id FROM clips WHERE streamer_id = ?", [streamer_id])]
    for cid in ids:
        db.execute("DELETE FROM clips_fts WHERE clip_id = ?", [cid])
    db.execute("DELETE FROM jobs WHERE streamer_id = ? OR clip_id IN (SELECT id FROM clips WHERE streamer_id = ?)",
               [streamer_id, streamer_id])
    db.execute("DELETE FROM streamers WHERE id = ?", [streamer_id])
    search.invalidate_cache()
    return {"ok": True}


# ---------- clips ----------

FILTER_KEYS = ("streamer_id", "category", "game", "date_from", "date_to", "min_views", "max_duration",
               "min_energy", "mood", "starred", "verified_only", "needs_review", "status")


@app.get("/api/clips")
def list_clips(q: str = "", sort: str = "newest", limit: int = Query(60, le=200), offset: int = 0,
               streamer_id: int | None = None, category: str | None = None, game: str | None = None,
               date_from: str | None = None, date_to: str | None = None, min_views: int | None = None,
               max_duration: float | None = None, min_energy: int | None = None, mood: str | None = None,
               starred: bool = False, verified_only: bool = False, needs_review: bool = False,
               status: str | None = None):
    local = locals()
    filters = {k: local[k] for k in FILTER_KEYS if local.get(k)}
    return search.search(q, filters, sort, limit, offset)


@app.get("/api/facets")
def get_facets(streamer_id: int | None = None):
    return search.facets(streamer_id)


@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: str):
    clip = _clip_or_404(clip_id)
    clip["qa"] = db.query("SELECT * FROM qa WHERE clip_id = ? ORDER BY id", [clip_id])
    clip["job"] = db.query_one(
        "SELECT * FROM jobs WHERE clip_id = ? ORDER BY id DESC LIMIT 1", [clip_id])
    clip["has_file"] = bool(clip.get("file_path") and Path(clip["file_path"]).exists())
    return clip


@app.patch("/api/clips/{clip_id}")
def patch_clip(clip_id: str, body: dict = Body(...)):
    _clip_or_404(clip_id)
    if "starred" in body:
        db.update("clips", "id", clip_id, starred=int(bool(body["starred"])))
    if any(k in body for k in ("summary", "tags", "category")):
        analyze.correct(clip_id, body.get("summary"), body.get("tags"), body.get("category"))
    return get_clip(clip_id)


@app.post("/api/clips/{clip_id}/analyze")
def analyze_clip(clip_id: str, body: dict = Body(default={})):
    _clip_or_404(clip_id)
    return {"job_id": worker.enqueue_analyze(clip_id, body.get("provider"), priority=True)}


@app.post("/api/clips/analyze")
def analyze_many(body: dict = Body(...)):
    """Queue many clips: explicit ids, or everything not yet analysed (optionally one streamer)."""
    ids = body.get("ids")
    if not ids:
        where, params = "status IN ('new','error')", []
        if body.get("streamer_id"):
            where += " AND streamer_id = ?"
            params.append(int(body["streamer_id"]))
        ids = [r["id"] for r in db.query(f"SELECT id FROM clips WHERE {where} ORDER BY view_count DESC", params)]
    for cid in ids:
        worker.enqueue_analyze(cid, body.get("provider"))
    return {"queued": len(ids)}


@app.post("/api/clips/{clip_id}/verify")
def verify(clip_id: str, body: dict = Body(default={})):
    _clip_or_404(clip_id)
    provider = body.get("provider")
    kind = "crosscheck" if provider and provider != config.get_settings().ai_mode else "verify"
    try:
        analyze.verify_clip(clip_id, get_provider(provider), kind)
    except AIError as e:
        _bad(e)
    return get_clip(clip_id)


@app.post("/api/clips/{clip_id}/ask")
def ask(clip_id: str, body: dict = Body(...)):
    question = (body.get("question") or "").strip()
    if not question:
        _bad(ValueError("Ask something"))
    _clip_or_404(clip_id)
    try:
        return analyze.ask(clip_id, question, body.get("provider"))
    except AIError as e:
        _bad(e)


@app.post("/api/clips/{clip_id}/reveal")
def reveal(clip_id: str):
    clip = _clip_or_404(clip_id)
    path = clip.get("file_path")
    if not path or not Path(path).exists():
        _bad(ValueError("Clip isn't downloaded yet"))
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])
    return {"ok": True}


# ---------- jobs ----------

@app.get("/api/jobs")
def jobs(limit: int = 100):
    counts = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    rows = db.query(
        """SELECT j.*, c.title AS clip_title, c.thumbnail_url, s.display_name AS streamer_name
           FROM jobs j LEFT JOIN clips c ON c.id = j.clip_id
           LEFT JOIN streamers s ON s.id = COALESCE(j.streamer_id, c.streamer_id)
           ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN j.status IN ('queued') THEN j.created_at END ASC,
                    j.updated_at DESC
           LIMIT ?""", [limit])
    return {"counts": counts, "jobs": rows}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    worker.cancel(job_id)
    return {"ok": True}


@app.post("/api/jobs/cancel-queued")
def cancel_queued():
    return {"cancelled": worker.cancel_all_queued()}


@app.post("/api/jobs/retry-failed")
def retry_failed():
    ids = [r["id"] for r in db.query("SELECT id FROM clips WHERE status = 'error'")]
    for cid in ids:
        worker.enqueue_analyze(cid)
    db.execute("DELETE FROM jobs WHERE status = 'error'")
    return {"queued": len(ids)}


@app.post("/api/jobs/clear-finished")
def clear_finished():
    db.execute("DELETE FROM jobs WHERE status IN ('done','cancelled','error')")
    return {"ok": True}


# ---------- DaVinci Resolve ----------

def _resolve_item(clip: dict, append: bool = False, queue_id: int | None = None) -> dict:
    a = clip.get("analysis") or {}
    return {
        "clip_id": clip["id"], "queue_id": queue_id, "append": append,
        "path": clip.get("file_path"), "title": clip.get("title"), "summary": clip.get("summary"),
        "tags": clip.get("tags") or [], "category": clip.get("category"),
        "streamer": clip.get("streamer_name"), "moments": a.get("moments") or [],
        "best_in": a.get("best_in"), "best_out": a.get("best_out"),
    }


@app.post("/api/resolve/send")
def resolve_send(body: dict = Body(...)):
    clips = [_clip_or_404(cid) for cid in body.get("ids") or []]
    try:
        res = resolve_bridge.get_resolve()
        return resolve_bridge.push_clips(res, [_resolve_item(c) for c in clips],
                                         config.get_settings().resolve_bin_root, bool(body.get("append")))
    except resolve_bridge.ResolveError as e:
        _bad(e)


@app.post("/api/resolve/queue")
def resolve_queue_add(body: dict = Body(...)):
    ids = body.get("ids") or []
    for cid in ids:
        _clip_or_404(cid)
        db.execute("INSERT INTO resolve_queue (clip_id, append_to_timeline, created_at) VALUES (?,?,?)",
                   [cid, int(bool(body.get("append"))), db.now()])
    return {"queued": len(ids)}


@app.get("/api/resolve/queue")
def resolve_queue():
    rows = db.query("SELECT * FROM resolve_queue WHERE sent_at IS NULL ORDER BY id")
    items = []
    for r in rows:
        clip = analyze.get_clip(r["clip_id"])
        if clip:
            items.append(_resolve_item(clip, bool(r["append_to_timeline"]), r["id"]))
    return {"items": items, "bin_root": config.get_settings().resolve_bin_root}


@app.post("/api/resolve/queue/ack")
def resolve_ack(body: dict = Body(...)):
    for qid in body.get("ids") or []:
        db.execute("UPDATE resolve_queue SET sent_at = ? WHERE id = ?", [db.now(), qid])
    return {"ok": True}


@app.get("/api/resolve/bridge.py", response_class=PlainTextResponse)
def resolve_bridge_source():
    return Path(resolve_bridge.__file__).read_text("utf-8")


# ---------- media ----------

@app.get("/media/clip/{clip_id}")
def media_clip(clip_id: str):
    clip = _clip_or_404(clip_id)
    path = clip.get("file_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "Not downloaded")
    return FileResponse(path, media_type="video/mp4", filename=Path(path).name,
                        content_disposition_type="inline")


@app.get("/media/frame/{clip_id}/{index}")
def media_frame(clip_id: str, index: int):
    clip = _clip_or_404(clip_id)
    frames = clip.get("frames") or []
    if index < 0 or index >= len(frames) or not Path(frames[index]["path"]).exists():
        raise HTTPException(404, "No frame")
    return FileResponse(frames[index]["path"], media_type="image/jpeg")


# ---------- UI ----------

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = FRONTEND_DIST / path
        if path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
