from app.analyze import apply_verification, normalize_analysis


def test_normalize_cleans_model_output():
    raw = {"summary": " He dies ", "category": "Nonsense", "tags": ["#Death", "death", "Boss"],
           "moments": [{"t": 99, "description": "late"}, {"t": 2, "description": "early"}, {"t": 1}],
           "energy": 9, "confidence": 3, "best_in": 10, "best_out": 5}
    a = normalize_analysis(raw, 20)
    assert a["category"] == "Other"
    assert a["tags"] == ["death", "boss"]
    assert [m["t"] for m in a["moments"]] == [2.0, 20.0]
    assert a["energy"] == 5 and a["confidence"] == 1.0
    assert (a["best_in"], a["best_out"]) == (0.0, 20.0)
    assert a["summary"] == "He dies"


def test_verification_removes_tags_and_flags_review():
    a = normalize_analysis({"summary": "x", "category": "Fail", "tags": ["death", "boss"], "confidence": 0.9}, 30)
    v = {"checks": [{"supported": "yes"}, {"supported": "no"}], "remove_tags": ["Boss"],
         "corrected_summary": "fixed", "confidence": 0.8}
    out, conf, review = apply_verification(a, v, 0.6)
    assert out["tags"] == ["death"]
    assert out["summary"] == "fixed"
    assert review is True
    assert 0 < conf < 0.8


def test_verification_all_supported_keeps_confidence():
    a = normalize_analysis({"summary": "x", "category": "Fail", "confidence": 0.9}, 30)
    _, conf, review = apply_verification(a, {"checks": [{"supported": "yes"}], "confidence": 0.95}, 0.6)
    assert conf == 0.9 and review is False
