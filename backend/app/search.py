"""Hybrid search: keyword (SQLite FTS5) + meaning (embeddings), merged with reciprocal-rank fusion."""
from __future__ import annotations

import re
import threading

import numpy as np

from . import db, embed

RRF_K = 60
VECTOR_TOP = 80
VECTOR_MIN_SIM = 0.35
SORTS = {
    "newest": "c.created_at DESC",
    "oldest": "c.created_at ASC",
    "views": "c.view_count DESC",
    "energy": "COALESCE(c.energy, 0) DESC, c.view_count DESC",
    "longest": "c.duration DESC",
}

_cache_lock = threading.Lock()
_cache: tuple[list[str], np.ndarray] | None = None


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _vectors() -> tuple[list[str], np.ndarray]:
    global _cache
    with _cache_lock:
        if _cache is None:
            rows = db.connect().execute("SELECT id, embedding FROM clips WHERE embedding IS NOT NULL").fetchall()
            ids = [r["id"] for r in rows]
            mat = (np.stack([embed.from_blob(r["embedding"]) for r in rows])
                   if rows else np.zeros((0, embed.DIM), dtype=np.float32))
            _cache = (ids, mat)
        return _cache


def tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"[\w']+", q.lower(), flags=re.UNICODE) if len(t) > 1]


def fts_query(q: str) -> str:
    return " OR ".join(f'"{t.replace(chr(34), "")}"*' for t in tokens(q))


def _where(f: dict) -> tuple[str, list]:
    clauses, params = ["1=1"], []
    if f.get("streamer_id"):
        clauses.append("c.streamer_id = ?"); params.append(int(f["streamer_id"]))
    if f.get("category"):
        clauses.append("c.category = ?"); params.append(f["category"])
    if f.get("game"):
        clauses.append("c.game_name = ?"); params.append(f["game"])
    if f.get("date_from"):
        clauses.append("c.created_at >= ?"); params.append(f["date_from"])
    if f.get("date_to"):
        clauses.append("c.created_at <= ?"); params.append(f["date_to"] + "T23:59:59Z")
    if f.get("min_views"):
        clauses.append("c.view_count >= ?"); params.append(int(f["min_views"]))
    if f.get("max_duration"):
        clauses.append("c.duration <= ?"); params.append(float(f["max_duration"]))
    if f.get("min_energy"):
        clauses.append("c.energy >= ?"); params.append(int(f["min_energy"]))
    if f.get("mood"):
        clauses.append("c.mood = ?"); params.append(f["mood"])
    if f.get("starred"):
        clauses.append("c.starred = 1")
    if f.get("verified_only"):
        clauses.append("c.status = 'done' AND c.needs_review = 0")
    if f.get("needs_review"):
        clauses.append("c.needs_review = 1")
    status = f.get("status")
    if status == "analyzed":
        clauses.append("c.status = 'done'")
    elif status == "pending":
        clauses.append("c.status != 'done'")
    return " AND ".join(clauses), params


BASE = """SELECT c.*, s.login AS streamer_login, s.display_name AS streamer_name
          FROM clips c JOIN streamers s ON s.id = c.streamer_id"""


def best_moment(clip: dict, q_tokens: list[str]) -> dict | None:
    """Find the timestamp inside the clip that best matches the query."""
    if not q_tokens:
        return None
    candidates = [(m["t"], m["description"]) for m in (clip.get("analysis") or {}).get("moments") or []]
    candidates += [(s["start"], s["text"]) for s in clip.get("transcript") or []]
    best, best_score = None, 0
    for t, text in candidates:
        words = set(tokens(text))
        score = sum(1 for qt in q_tokens if any(w.startswith(qt) for w in words))
        if score > best_score:
            best, best_score = {"t": t, "text": text}, score
    return best


def _slim(clip: dict) -> dict:
    """Drop heavy fields for list views."""
    for k in ("transcript", "frames", "verification"):
        clip.pop(k, None)
    return clip


def search(q: str = "", filters: dict | None = None, sort: str = "newest",
           limit: int = 60, offset: int = 0) -> dict:
    filters = filters or {}
    where, params = _where(filters)
    q = (q or "").strip()

    if not q:
        order = SORTS.get(sort, SORTS["newest"])
        total = db.query_one(f"SELECT COUNT(*) AS n FROM clips c WHERE {where}", params)["n"]
        rows = db.query(f"{BASE} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset])
        return {"total": total, "results": [_slim(r) for r in rows], "mode": "browse"}

    allowed = {r["id"] for r in db.query(f"SELECT c.id FROM clips c WHERE {where}", params)}
    scores: dict[str, float] = {}

    # Keyword ranking
    fq = fts_query(q)
    if fq:
        hits = db.query(
            "SELECT clip_id FROM clips_fts WHERE clips_fts MATCH ? ORDER BY bm25(clips_fts, 0, 3.0, 2.0, 2.0, 1.0, 1.0) LIMIT 300",
            [fq],
        )
        rank = 0
        for h in hits:
            if h["clip_id"] in allowed:
                scores[h["clip_id"]] = scores.get(h["clip_id"], 0) + 1 / (RRF_K + rank)
                rank += 1

    # Meaning ranking
    mode = "keyword"
    ids, mat = _vectors()
    if len(ids):
        qv = embed.embed([q], query=True)
        if qv is not None:
            mode = "hybrid"
            sims = mat @ qv[0]
            order = np.argsort(-sims)
            rank = 0
            for i in order:
                if rank >= VECTOR_TOP or sims[i] < VECTOR_MIN_SIM:
                    break
                if ids[i] in allowed:
                    scores[ids[i]] = scores.get(ids[i], 0) + 1 / (RRF_K + rank)
                    rank += 1

    ranked = sorted(scores, key=lambda k: -scores[k])
    page = ranked[offset:offset + limit]
    results = []
    if page:
        marks = ",".join("?" * len(page))
        by_id = {r["id"]: r for r in db.query(f"{BASE} WHERE c.id IN ({marks})", page)}
        q_tokens = tokens(q)
        for cid in page:
            clip = by_id.get(cid)
            if not clip:
                continue
            clip["match"] = best_moment(clip, q_tokens)
            clip["score"] = round(scores[cid], 5)
            results.append(_slim(clip))
    return {"total": len(ranked), "results": results, "mode": mode}


def facets(streamer_id: int | None = None) -> dict:
    where, params = ("c.streamer_id = ?", [streamer_id]) if streamer_id else ("1=1", [])
    cats = db.query(f"SELECT category AS name, COUNT(*) AS n FROM clips c WHERE {where} AND category IS NOT NULL GROUP BY category ORDER BY n DESC", params)
    games = db.query(f"SELECT game_name AS name, COUNT(*) AS n FROM clips c WHERE {where} AND game_name IS NOT NULL AND game_name != '' GROUP BY game_name ORDER BY n DESC LIMIT 40", params)
    moods = db.query(f"SELECT mood AS name, COUNT(*) AS n FROM clips c WHERE {where} AND mood IS NOT NULL AND mood != '' GROUP BY mood ORDER BY n DESC LIMIT 20", params)
    counts = db.query_one(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS analyzed,
                   SUM(needs_review) AS needs_review,
                   SUM(starred) AS starred
            FROM clips c WHERE {where}""", params)
    return {"categories": cats, "games": games, "moods": moods,
            "counts": {k: v or 0 for k, v in counts.items()}}
