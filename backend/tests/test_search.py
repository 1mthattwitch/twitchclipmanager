import numpy as np

from app import embed, search
from conftest import add_clip, add_streamer


def seed():
    sid = add_streamer("alpha")
    other = add_streamer("beta")
    add_clip(sid, "a1", "LOL", summary="Streamer screams at a jumpscare in a horror game",
             category="Jumpscare / Scary", tags='["jumpscare", "scream"]', energy=5, status="done",
             analysis='{"moments": [{"t": 3.5, "description": "monster jumps out"}, {"t": 7, "description": "screams"}]}')
    add_clip(sid, "a2", "clutch 1v4", summary="Wins a 1v4 round with a pistol", category="Clutch / Highlight",
             tags='["clutch", "ace"]', energy=4, status="done", view_count=500)
    add_clip(other, "b1", "cooking stream", summary="Burns the pancakes", category="Fail", status="done")
    return sid


def test_keyword_search_finds_by_ai_description():
    seed()
    res = search.search("jumpscare")
    assert [r["id"] for r in res["results"]] == ["a1"]
    # "jumpscare" is in the summary, not in any timed moment, so there's no timestamp to jump to.
    assert res["results"][0]["match"] is None


def test_moment_match_points_at_timestamp():
    seed()
    res = search.search("screams")
    assert res["results"][0]["id"] == "a1"
    assert res["results"][0]["match"]["t"] == 7


def test_filters_apply_to_search_and_browse():
    sid = seed()
    assert search.search("", {"streamer_id": sid})["total"] == 2
    assert search.search("", {"category": "Fail"})["results"][0]["id"] == "b1"
    assert search.search("", sort="views")["results"][0]["id"] == "a2"
    assert search.search("pancakes", {"streamer_id": sid})["total"] == 0


def test_vector_search_finds_meaning_without_keywords(monkeypatch):
    vocab = ["scared", "jumpscare", "horror", "win", "clutch", "food", "pancakes"]
    synonyms = {"frightened": "jumpscare", "spooky": "horror"}

    def fake_embed(texts, query=False):
        out = []
        for t in texts:
            words = [synonyms.get(w, w) for w in t.lower().replace(",", " ").split()]
            v = np.array([sum(w.startswith(k) for w in words) for k in vocab], dtype=np.float32) + 1e-3
            out.append(v / np.linalg.norm(v))
        return np.stack(out)

    monkeypatch.setattr(embed, "embed", fake_embed)
    monkeypatch.setattr(embed, "DIM", len(vocab))
    seed()
    from app import analyze
    for cid in ("a1", "a2", "b1"):
        analyze.reembed(cid)
    res = search.search("frightened spooky")
    assert res["mode"] == "hybrid"
    assert res["results"][0]["id"] == "a1"


def test_facets():
    seed()
    f = search.facets()
    assert f["counts"]["total"] == 3
    assert {c["name"] for c in f["categories"]} == {"Jumpscare / Scary", "Clutch / Highlight", "Fail"}
