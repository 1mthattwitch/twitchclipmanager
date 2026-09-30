"""Background job runner.

Two lanes: "net" for Twitch syncing (network-bound) and "gpu" for clip analysis,
which runs one clip at a time so Whisper and the local vision model don't fight
over VRAM.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import analyze, config, db
from .twitch import CLIPS_EPOCH, TwitchClient, iso, parse_iso

LANES = {"net": ("sync",), "gpu": ("analyze", "verify")}
IDLE_SLEEP = 2.0
RECHECK_EVERY = 24 * 3600
MAX_ATTEMPTS = 3
RETRY_DELAY = 5.0

_stop = threading.Event()
_wake = threading.Event()
_threads: list[threading.Thread] = []


# ---------- enqueueing ----------

def _insert_job(kind: str, clip_id: str | None = None, streamer_id: int | None = None,
                params: dict | None = None) -> int:
    t = db.now()
    cur = db.execute(
        "INSERT INTO jobs (kind, clip_id, streamer_id, params, status, created_at, updated_at) VALUES (?,?,?,?, 'queued', ?, ?)",
        [kind, clip_id, streamer_id, json.dumps(params or {}), t, t],
    )
    _wake.set()
    return cur.lastrowid


def enqueue_sync(streamer_id: int, since_days: int | None = None, full: bool = False) -> int:
    existing = db.query_one(
        "SELECT id FROM jobs WHERE kind='sync' AND streamer_id=? AND status IN ('queued','running')", [streamer_id])
    if existing:
        return existing["id"]
    return _insert_job("sync", streamer_id=streamer_id, params={"since_days": since_days, "full": full})


def enqueue_analyze(clip_id: str, provider: str | None = None, priority: bool = False) -> int:
    existing = db.query_one(
        "SELECT id FROM jobs WHERE kind='analyze' AND clip_id=? AND status IN ('queued','running')", [clip_id])
    if existing:
        return existing["id"]
    db.update("clips", "id", clip_id, status="queued", error=None)
    job_id = _insert_job("analyze", clip_id=clip_id, params={"provider": provider})
    if priority:
        # Jump the queue: jobs run oldest-first, so backdate this one.
        db.execute("UPDATE jobs SET created_at = 0 WHERE id = ?", [job_id])
    return job_id


def enqueue_verify(clip_id: str, provider: str | None = None, kind: str = "verify") -> int:
    return _insert_job("verify", clip_id=clip_id, params={"provider": provider, "kind": kind})


def cancel(job_id: int) -> None:
    job = db.query_one("SELECT * FROM jobs WHERE id = ?", [job_id])
    if job and job["status"] == "queued":
        db.update("jobs", "id", job_id, status="cancelled", updated_at=db.now())
        if job["clip_id"] and job["kind"] == "analyze":
            db.execute("UPDATE clips SET status='new' WHERE id=? AND status='queued'", [job["clip_id"]])


def cancel_all_queued() -> int:
    db.execute("UPDATE clips SET status='new' WHERE status='queued'")
    return db.execute("UPDATE jobs SET status='cancelled' WHERE status='queued' AND kind != 'sync'").rowcount


# ---------- running ----------

def _progress(job_id: int):
    def report(pct: float, msg: str):
        db.update("jobs", "id", job_id, progress=round(pct, 3), message=msg, updated_at=db.now())
    return report


def upsert_clip(streamer_id: int, c: dict, games: dict[str, str]) -> bool:
    """Insert or refresh one Twitch clip. Returns True if it was new."""
    exists = db.query_one("SELECT id FROM clips WHERE id = ?", [c["id"]])
    if exists:
        db.execute("UPDATE clips SET view_count = ?, title = ? WHERE id = ?",
                   [c.get("view_count", 0), c.get("title", ""), c["id"]])
        return False
    db.execute(
        """INSERT INTO clips (id, streamer_id, url, title, creator_name, game_id, game_name, view_count,
                              created_at, duration, thumbnail_url, vod_id, vod_offset, language, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new')""",
        [c["id"], streamer_id, c.get("url", f"https://clips.twitch.tv/{c['id']}"), c.get("title", ""),
         c.get("creator_name"), c.get("game_id"), games.get(c.get("game_id") or "", ""),
         c.get("view_count", 0), c.get("created_at"), c.get("duration", 0), c.get("thumbnail_url"),
         c.get("video_id") or None, c.get("vod_offset"), c.get("language")],
    )
    db.reindex_clip(c["id"])
    return True


def run_sync(job: dict, client: TwitchClient | None = None) -> str:
    params = job.get("params") or {}
    streamer = db.query_one("SELECT * FROM streamers WHERE id = ?", [job["streamer_id"]])
    if not streamer:
        raise ValueError("Streamer was removed")
    client = client or TwitchClient()
    # A minute of slack: Twitch's ended_at is exclusive and we send whole seconds.
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    if params.get("since_days"):
        start = end - timedelta(days=int(params["since_days"]))
    elif streamer.get("synced_until") and not params.get("full"):
        # Re-scan a few days back so view counts on recent clips stay fresh.
        start = parse_iso(streamer["synced_until"]) - timedelta(days=3)
    else:
        start = CLIPS_EPOCH
    report = _progress(job["id"])
    auto = config.get_settings().auto_analyze_new_clips
    new = seen = 0
    for batch in client.sweep(streamer["twitch_id"], start, end, progress=report):
        if _stop.is_set():
            break
        games = client.get_games([c.get("game_id") for c in batch])
        for c in batch:
            seen += 1
            if upsert_clip(streamer["id"], c, games):
                new += 1
                if auto:
                    enqueue_analyze(c["id"])
        report(_job_progress(job["id"]), f"Found {seen} clips ({new} new)")
    db.update("streamers", "id", streamer["id"], synced_until=iso(end))
    return f"Found {seen} clips, {new} new"


def _job_progress(job_id: int) -> float:
    row = db.query_one("SELECT progress FROM jobs WHERE id = ?", [job_id])
    return row["progress"] if row else 0.0


def run_job(job: dict) -> str:
    params = job.get("params") or {}
    if job["kind"] == "sync":
        return run_sync(job)
    if job["kind"] == "analyze":
        try:
            analyze.run_pipeline(job["clip_id"], params.get("provider"), _progress(job["id"]))
        except Exception as e:
            db.update("clips", "id", job["clip_id"], status="error", error=str(e))
            raise
        return "Analysed"
    if job["kind"] == "verify":
        from .ai.base import get_provider
        analyze.verify_clip(job["clip_id"], get_provider(params.get("provider")), params.get("kind", "verify"))
        return "Re-checked"
    raise ValueError(f"Unknown job kind {job['kind']}")


def _next_job(kinds: tuple[str, ...]) -> dict | None:
    marks = ",".join("?" * len(kinds))
    conn = db.connect()
    # Claim atomically so two lanes never grab the same job.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            f"SELECT * FROM jobs WHERE status='queued' AND kind IN ({marks}) ORDER BY created_at, id LIMIT 1",
            kinds).fetchone()
        if row:
            conn.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", [db.now(), row["id"]])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return db.row_to_dict(row)


def _maybe_recheck() -> bool:
    """When idle, re-verify one low-confidence clip that hasn't been re-checked today."""
    if not config.get_settings().background_recheck:
        return False
    clip = db.query_one(
        """SELECT c.id FROM clips c WHERE c.needs_review = 1 AND c.corrected = 0 AND c.status = 'done'
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.clip_id = c.id AND j.kind = 'verify'
                           AND j.created_at > ?)
           ORDER BY c.view_count DESC LIMIT 1""",
        [db.now() - RECHECK_EVERY])
    if not clip:
        return False
    enqueue_verify(clip["id"])
    return True


def _loop(lane: str) -> None:
    kinds = LANES[lane]
    idle_since = time.time()
    while not _stop.is_set():
        try:
            job = _next_job(kinds)
        except Exception:
            traceback.print_exc()
            job = None
        if not job:
            if lane == "gpu" and time.time() - idle_since > 30:
                idle_since = time.time()
                try:
                    if _maybe_recheck():
                        continue
                except Exception:
                    traceback.print_exc()
            _wake.wait(IDLE_SLEEP)
            _wake.clear()
            continue
        try:
            msg = run_job(job)
            db.update("jobs", "id", job["id"], status="done", progress=1.0, message=msg, updated_at=db.now())
        except Exception as e:
            params = job.get("params") or {}
            attempts = int(params.get("attempts", 0)) + 1
            if getattr(e, "transient", False) and attempts < MAX_ATTEMPTS:
                # Back off, then put it back in the queue behind other work.
                params["attempts"] = attempts
                db.update("jobs", "id", job["id"], status="queued", params=params, progress=0,
                          message=f"{e} (retry {attempts}/{MAX_ATTEMPTS - 1})", created_at=db.now(),
                          updated_at=db.now())
                if job["clip_id"] and job["kind"] == "analyze":
                    db.update("clips", "id", job["clip_id"], status="queued")
                _stop.wait(RETRY_DELAY * attempts)
            else:
                traceback.print_exc()
                db.update("jobs", "id", job["id"], status="error", message=str(e)[:500], updated_at=db.now())
        idle_since = time.time()


def start() -> None:
    db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    db.execute("UPDATE clips SET status='queued' WHERE status IN ('downloading','transcribing','analyzing')")
    _stop.clear()
    for lane in LANES:
        t = threading.Thread(target=_loop, args=(lane,), name=f"worker-{lane}", daemon=True)
        t.start()
        _threads.append(t)


def stop() -> None:
    _stop.set()
    _wake.set()
