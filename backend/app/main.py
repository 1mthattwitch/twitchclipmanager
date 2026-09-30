"""FastAPI app: JSON API under /api, media under /media, the built UI at /."""
from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import analyze, config, db, embed, media, resolve_bridge, search, transcribe, worker
from .ai.base import AIError, get_provider
from .ai.prompts import CATEGORIES
from .services import diagnostics, ollama_manager, resolve_setup
from .twitch import TwitchClient, TwitchError

FRONTEND_DIST = config.ROOT_DIR / "frontend" / "dist"


def _setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(config.DATA_DIR / "app.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _autostart_ollama() -> None:
    """In offline mode, start Ollama (with the chosen model folder) if it's installed but not running."""
    try:
        if config.get_settings().ai_mode == "local" and ollama_manager.find_exe():
            ollama_manager.ensure_running()
    except Exception:
        logging.getLogger(__name__).exception("Couldn't auto-start Ollama")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    logging.getLogger(__name__).info("Clip Manager starting")
    db.connect()
    if os.environ.get("TCM_NO_WORKER") != "1":
        worker.start()
        threading.Thread(target=_autostart_ollama, daemon=True).start()
        # Load (or first-time download) the search model without blocking startup.
        threading.Thread(target=embed.available, daemon=True).start()
    yield
    worker.stop()


app = FastAPI(title="Twitch Clip Manager", lifespan=lifespan)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
ACTION_HEADER = "x-clip-manager"


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Only this computer's browser may use the app.

    The Host check stops DNS-rebinding tricks; the custom header on actions stops other
    websites from making your browser POST here (browsers can't add it cross-site).
    """
    host = (request.headers.get("host") or "").strip()
    hostname = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
    if hostname.lower() not in LOCAL_HOSTS:
        return JSONResponse({"detail": "Only available from this computer."}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(ACTION_HEADER) != "1":
        return JSONResponse({"detail": "Missing X-Clip-Manager header."}, status_code=403)
    return await call_next(request)


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
    out["whisper"] = importlib.util.find_spec("faster_whisper") is not None
    out["embeddings_state"] = embed.status()
    out["embeddings"] = out["embeddings_state"] == "ready"
    out["whisper_error"] = transcribe.last_error()
    out["whisper_device"] = transcribe.device_in_use()
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


def _ensure_file(clip: dict) -> dict:
    """Videos aren't kept after analysis by default: fetch one again when it's needed."""
    if clip.get("file_path") and Path(clip["file_path"]).exists():
        return clip
    try:
        path = media.download(clip, clip["streamer_login"])
    except media.MediaError as e:
        _bad(e)
    db.update("clips", "id", clip["id"], file_path=str(path))
    return {**clip, "file_path": str(path)}


@app.post("/api/clips/{clip_id}/reveal")
def reveal(clip_id: str):
    clip = _ensure_file(_clip_or_404(clip_id))
    path = clip["file_path"]
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
    waiting = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND kind='analyze'")["n"]
    return {"counts": counts, "jobs": rows, "paused": worker.paused() or None,
            "error_groups": worker.error_groups(), "eta_seconds": worker.estimate_seconds(waiting)}


@app.post("/api/jobs/pause")
def pause_jobs():
    worker.pause()
    return {"ok": True}


@app.post("/api/jobs/resume")
def resume_jobs():
    worker.resume()
    return {"ok": True}


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
    worker.resume()
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
        clips = [_ensure_file(c) for c in clips]
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
    # Videos aren't kept after analysis by default; fetch them now so Resolve's script
    # finds them ready.
    threading.Thread(target=_fetch_files, args=(list(ids),), daemon=True).start()
    return {"queued": len(ids)}


def _fetch_files(ids: list[str]) -> None:
    for cid in ids:
        clip = analyze.get_clip(cid)
        if not clip:
            continue
        try:
            _ensure_file(clip)
        except Exception as e:
            logging.getLogger(__name__).warning("Couldn't download %s for Resolve: %s", cid, getattr(e, "detail", e))


@app.get("/api/resolve/queue")
def resolve_queue():
    rows = db.query("SELECT * FROM resolve_queue WHERE sent_at IS NULL ORDER BY id")
    items, waiting = [], 0
    for r in rows:
        clip = analyze.get_clip(r["clip_id"])
        if clip and not (clip.get("file_path") and Path(clip["file_path"]).exists()):
            waiting += 1  # still downloading; picked up on the next run
            continue
        if clip:
            items.append(_resolve_item(clip, bool(r["append_to_timeline"]), r["id"]))
    return {"items": items, "waiting": waiting, "bin_root": config.get_settings().resolve_bin_root}


@app.post("/api/resolve/queue/ack")
def resolve_ack(body: dict = Body(...)):
    for qid in body.get("ids") or []:
        db.execute("UPDATE resolve_queue SET sent_at = ? WHERE id = ?", [db.now(), qid])
    return {"ok": True}


@app.get("/api/resolve/bridge.py", response_class=PlainTextResponse)
def resolve_bridge_source():
    return Path(resolve_bridge.__file__).read_text("utf-8")


# ---------- setup: Ollama, Resolve, keys, diagnostics ----------

@app.get("/api/ollama/status")
def ollama_status():
    return ollama_manager.status()


@app.post("/api/ollama/use")
def ollama_use(body: dict = Body(...)):
    """Choose the model folder + model; sets OLLAMA_MODELS system-wide as the user asked."""
    done = ollama_manager.apply_choice(body.get("store") or None, body.get("model") or ollama_manager.DEFAULT_MODEL)
    return {"done": done, "status": ollama_manager.status()}


@app.post("/api/ollama/start")
def ollama_start():
    if not ollama_manager.start():
        _bad(ValueError("Ollama didn't start. Is it installed? Get it from ollama.com/download"))
    return ollama_manager.status()


@app.post("/api/ollama/restart")
def ollama_restart(body: dict = Body(default={})):
    store = body.get("store") or config.get_settings().ollama_models_dir
    if not ollama_manager.restart(store):
        _bad(ValueError("Ollama didn't come back after restarting. Open it from the Start menu."))
    return ollama_manager.status()


@app.post("/api/ollama/pull")
def ollama_pull(body: dict = Body(default={})):
    if not ollama_manager.ensure_running():
        _bad(ValueError("Ollama isn't running and couldn't be started."))
    ollama_manager.pull_in_background(body.get("model") or config.get_settings().ollama_model)
    return ollama_manager.pull_state()


@app.get("/api/resolve/status")
def resolve_status():
    return resolve_setup.script_status()


@app.post("/api/resolve/install-script")
def resolve_install_script():
    try:
        return resolve_setup.install_script()
    except OSError as e:
        _bad(ValueError(f"Couldn't install the Resolve script: {e}"))


@app.post("/api/resolve/test")
def resolve_test():
    return resolve_setup.check_connection()


@app.post("/api/setup/test-twitch")
def setup_test_twitch(body: dict = Body(default={})):
    s = config.get_settings()
    cid = body.get("client_id") or s.twitch_client_id
    secret = body.get("client_secret")
    if not secret or secret.startswith("••"):
        secret = s.twitch_client_secret
    try:
        TwitchClient(cid, secret)._get_token()
    except TwitchError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach Twitch: {e}"}
    return {"ok": True, "message": "Twitch accepted your keys."}


@app.post("/api/setup/test-claude")
def setup_test_claude(body: dict = Body(default={})):
    import anthropic
    key = body.get("api_key")
    if not key or key.startswith("••"):
        key = config.get_settings().anthropic_api_key
    if not key:
        return {"ok": False, "message": "Paste your API key first."}
    try:
        anthropic.Anthropic(api_key=key, max_retries=0, timeout=15).models.list(limit=1)
    except anthropic.AuthenticationError:
        return {"ok": False, "message": "Claude rejected that key. Copy it again from console.anthropic.com."}
    except anthropic.APIConnectionError:
        return {"ok": False, "message": "Couldn't reach Claude. Check your internet connection."}
    except anthropic.APIStatusError as e:
        return {"ok": False, "message": f"Claude answered {e.status_code}: {e.message}"}
    return {"ok": True, "message": "Claude accepted your key."}


@app.post("/api/setup/complete")
def setup_complete(body: dict = Body(default={})):
    config.save_settings({"setup_complete": bool(body.get("complete", True))})
    return {"ok": True}


@app.get("/api/diagnostics", response_class=PlainTextResponse)
def get_diagnostics():
    return diagnostics.report(status())


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
