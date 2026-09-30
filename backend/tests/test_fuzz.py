"""Robustness: whatever a model or a user throws at us, nothing crashes."""
import json
import random
import string

import pytest

from app import search
from app.ai.base import AIError, parse_json_loose
from app.analyze import apply_verification, normalize_analysis
from app.bench_data import seed
from conftest import add_clip, add_streamer

JUNK = [None, "", "   ", 0, -5, 1e9, float("nan"), True, [], {}, [None], ["x", 3, None], {"a": 1},
        "Funny", "funny", "∞", "🔥🔥", "a" * 5000, -0.0, "7", "0.4"]


def random_value(rng, depth=0):
    kind = rng.randrange(6 if depth < 2 else 4)
    if kind == 0:
        return rng.choice(JUNK)
    if kind == 1:
        return "".join(rng.choice(string.printable) for _ in range(rng.randrange(40)))
    if kind == 2:
        return rng.uniform(-1e6, 1e6)
    if kind == 3:
        return rng.randrange(-10, 10)
    if kind == 4:
        return [random_value(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {k: random_value(rng, depth + 1) for k in rng.sample(["t", "description", "x", "supported"], 2)}


FIELDS = ["summary", "category", "secondary_categories", "tags", "moments", "on_screen", "mood",
          "energy", "profanity", "best_in", "best_out", "search_phrases", "confidence"]


@pytest.mark.parametrize("seed_value", range(400))
def test_normalize_and_verify_never_crash(seed_value):
    rng = random.Random(seed_value)
    raw = {k: random_value(rng) for k in rng.sample(FIELDS, rng.randrange(len(FIELDS) + 1))}
    dur = rng.choice([0, None, 5, 30.5, -3, 3600])
    a = normalize_analysis(raw, dur)
    json.dumps(a)  # must be storable
    assert 1 <= a["energy"] <= 5
    assert 0.0 <= a["confidence"] <= 1.0
    assert a["best_out"] > a["best_in"] >= 0
    assert all(isinstance(t, str) and t for t in a["tags"])
    v = {"checks": random_value(rng), "remove_tags": random_value(rng),
         "corrected_summary": random_value(rng), "confidence": random_value(rng)}
    out, conf, review = apply_verification(a, v, 0.6)
    assert 0.0 <= conf <= 1.0 and isinstance(review, bool)
    assert isinstance(out["summary"], str)


@pytest.mark.parametrize("text,ok", [
    ('{"a": 1}', True),
    ('Sure! Here it is:\n```json\n{"a": 1}\n```', True),
    ('prefix {"a": {"b": 2}} suffix', True),
    ("I can't see the clip.", False),
    ("", False),
    ("{broken", False),
])
def test_parse_json_loose(text, ok):
    if ok:
        assert isinstance(parse_json_loose(text), dict)
    else:
        with pytest.raises((AIError, ValueError)):
            parse_json_loose(text)


HOSTILE = ['"', '""', "*", "AND", "OR NOT", "NEAR(a b)", "a:b", "title:x", "^", "(((", ")", "-",
           "'; DROP TABLE clips; --", "🔥 clutch 🔥", "日本語", "ñandú", "a" * 3000, " ", "\\", "%", "_",
           "{}", "[]", "the the the", "clutch*", '"clutch"', "c02", "NULL", "\x00", "\n\t"]


def test_hostile_queries_never_crash():
    seed(add_streamer, add_clip)
    rng = random.Random(1)
    queries = HOSTILE + ["".join(rng.choice(string.printable) for _ in range(rng.randrange(30))) for _ in range(300)]
    for q in queries:
        res = search.search(q, {"category": rng.choice(["", "Fail"]), "min_views": rng.choice([0, 10])},
                            sort=rng.choice(["newest", "views", "bogus"]))
        assert isinstance(res["results"], list)


def test_api_rejects_bad_input_cleanly():
    from fastapi.testclient import TestClient
    from app.main import app
    seed(add_streamer, add_clip)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/clips/does-not-exist").status_code == 404
        assert c.post("/api/clips/does-not-exist/ask", json={"question": "hi"}).status_code == 404
        assert c.post("/api/clips/c01/ask", json={"question": ""}).status_code == 400
        # Not analysed with frames yet -> friendly 400, not a crash
        assert c.post("/api/clips/c01/ask", json={"question": "what?"}).status_code == 400
        assert c.post("/api/streamers", json={"login": ""}).status_code == 400
        # No Twitch keys configured -> friendly 400
        r = c.post("/api/streamers", json={"login": "someone"})
        assert r.status_code == 400 and "Twitch" in r.json()["detail"]
        assert c.get("/api/clips", params={"limit": 999}).status_code == 422
        assert c.get("/media/clip/c01").status_code == 404
        assert c.get("/media/frame/c01/99").status_code == 404
        for q in HOSTILE:
            assert c.get("/api/clips", params={"q": q}).status_code == 200, q
        r = c.post("/api/resolve/send", json={"ids": ["c01"]})
        assert r.status_code == 400  # Resolve not installed here: clear error, no crash
        assert c.patch("/api/clips/c01", json={"category": "Not a category"}).status_code == 200
        assert c.get("/api/clips/c01").json()["category"] == "Jumpscare / Scary"
