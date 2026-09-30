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


STOPWORDS = set("""
a an the and or but of to in on at by for with from into onto about over after before during while
is are was were be been being am do does did has have had it its it's this that these those there
he him his she her they them their we us our you your i me my mine who whom what which when where
why how all any some very just so than too up down out off then only own same can will would should
could get gets got getting moment clip clips stream streamer streaming video when where one someone
""".split())

# Streamer / editing vocabulary. Lets keyword search find clips the AI described with other words.
SYNONYM_GROUPS = [
    "scared scare scary frightened fear afraid terrified spooked spooky jumpscare horror creepy",
    "scream screams screaming yell yelling shout shouting shriek",
    "funny hilarious lol lmao laughing laugh joke comedy",
    "rage raging angry furious mad tilted tilt fuming",
    "fail fails failure mistake blunder throw throws oops accident",
    "clutch highlight insane cracked ace win wins victory",
    "dog dogs doggo puppy pup",
    "cat cats kitty kitten",
    "mom mum mother mama parent parents dad father family",
    "money donation donates donated dono tip bits",
    "raid raided raiders viewers join joins joined",
    "kitchen cooking cook food",
    "sing singing song karaoke",
    "dance dancing dances",
    "pistol handgun sheriff",
    "quit quitting break burnout retire",
    "muted mute unmute mic microphone",
    "record wr speedrun",
    "snake reptile",
    "sleep sleeping asleep napping nap",
    "crash crashed flips flip wreck",
    "cops cop police",
    "teammate teammates team squad duo",
    "chair seat",
    "own goal",
]
_SYNONYMS: dict[str, set[str]] = {}
for _group in SYNONYM_GROUPS:
    _words = _group.split()
    for _w in _words:
        _SYNONYMS.setdefault(_w, set()).update(_words)


def tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"[\w']+", q.lower(), flags=re.UNICODE) if len(t) > 1]


def query_terms(q: str) -> list[str]:
    toks = tokens(q)
    kept = [t for t in toks if t not in STOPWORDS]
    return kept or toks


def fts_query(q: str) -> str:
    """Each query word becomes (word OR its synonyms); words are OR-ed and bm25 ranks by coverage."""
    parts = []
    for t in query_terms(q):
        alts = sorted(_SYNONYMS.get(t, set()) | {t})
        ors = " OR ".join(f'"{a.replace(chr(34), "")}"*' for a in alts)
        parts.append(f"({ors})")
    return " OR ".join(parts)


BM25 = "bm25(clips_fts, 0, 3.0, 2.0, 2.0, 1.0, 1.0)"


def _fts_ids(match: str, limit: int = 1000) -> list[str]:
    try:
        return [r["clip_id"] for r in db.query(
            f"SELECT clip_id FROM clips_fts WHERE clips_fts MATCH ? ORDER BY {BM25} LIMIT ?", [match, limit])]
    except Exception:  # malformed FTS syntax from odd input: treat as no match
        return []


def keyword_ranking(q: str) -> list[str]:
    """Clips ordered by how many distinct query words they cover, then by bm25.

    A clip that matches "rage" AND "malenia" beats one that matches three synonyms of "rage".
    Exact word matches count fully, synonym-only matches count 0.6.
    """
    terms = query_terms(q)
    if not terms:
        return []
    base = _fts_ids(fts_query(q), 300)
    if len(terms) == 1 and not _SYNONYMS.get(terms[0]):
        return base
    order = {cid: i for i, cid in enumerate(base)}
    coverage = dict.fromkeys(base, 0.0)
    for t in terms:
        exact = set(_fts_ids(f'"{t.replace(chr(34), "")}"*'))
        alts = _SYNONYMS.get(t, set()) - {t}
        syn = set(_fts_ids(" OR ".join(f'"{a}"*' for a in alts))) if alts else set()
        for cid in coverage:
            coverage[cid] += 1.0 if cid in exact else 0.6 if cid in syn else 0.0
    return sorted(base, key=lambda c: (-coverage[c], order[c]))


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
    rank = 0
    for cid in keyword_ranking(q):
        if cid in allowed:
            scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank)
            rank += 1

    # Meaning ranking
    mode = "keyword"
    ids, mat = _vectors()
    if len(ids) and not embed.loading():  # don't make a search wait for a first-run download
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
        q_tokens = query_terms(q)
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
