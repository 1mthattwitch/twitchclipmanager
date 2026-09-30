import json

from app import analyze, config, db, evaluate
from app.bench_data import seed
from conftest import add_clip, add_streamer
from test_pipeline import StubProvider


def test_wilson_interval():
    lo, hi = evaluate.wilson(0, 30)
    assert lo == 0 and 0.05 < hi < 0.15
    lo, hi = evaluate.wilson(3, 30)
    assert lo < 0.1 < hi


def test_report_combines_all_signals(capsys):
    seed(add_streamer, add_clip)
    db.execute("UPDATE clips SET needs_review=1 WHERE id IN ('c01','c02')")
    db.execute("UPDATE clips SET corrected=1 WHERE id='c03'")
    for s in (1, 1, 1, 0):
        db.execute("INSERT INTO qa (clip_id, kind, question, answer, supported, created_at) VALUES ('c04','verify','q','a',?,0)", [s])
    (config.DATA_DIR / "spotcheck.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"clip": "c05", "category_ok": True, "summary_ok": True},
        {"clip": "c06", "category_ok": False, "summary_ok": True},
        {"clip": "c07", "category_ok": True, "summary_ok": True},
        {"clip": "c08", "category_ok": True, "summary_ok": True},
    ]))
    out = evaluate.report()
    assert out["analysed"] == 30
    assert abs(out["needs_review_rate"] - 2 / 30) < 1e-9
    assert out["claims_refuted_rate"] == 0.25
    assert out["spotcheck"]["error_rate"] == 0.25
    assert "Spot-check error rate" in capsys.readouterr().out


def test_agreement_with_stub(monkeypatch, tmp_path):
    seed(add_streamer, add_clip)
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    db.execute("UPDATE clips SET frames=?", [json.dumps([{"path": str(frame), "t": 1}])])
    db.execute("UPDATE clips SET category='Fail' WHERE id IN ('c01','c02','c03')")
    from app.ai import base
    monkeypatch.setattr(base, "get_provider", lambda name=None: StubProvider())
    out = evaluate.agreement(n=100, against="local")
    # The stub always says "Fail": agreement is exactly the clips already labelled Fail.
    fails = db.query_one("SELECT COUNT(*) n FROM clips WHERE category='Fail'")["n"]
    assert out["n"] == 30
    assert abs(out["disagreement"] - (1 - fails / 30)) < 1e-9


def test_search_bench_runs_in_isolation():
    before = config.DATA_DIR
    results = evaluate.search_bench()
    assert config.DATA_DIR == before
    assert results["normal"][1] >= 0.95
