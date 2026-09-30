from pathlib import Path

from app import analyze, db, media, transcribe
from app.ai import base
from conftest import add_clip, add_streamer


class StubProvider(base.Provider):
    name = "local"
    label = "stub"

    def __init__(self):
        self.calls = []

    def generate_json(self, system, prompt, images, schema):
        self.calls.append(("json", len(images)))
        if "checks" in schema["properties"]:
            return {"checks": [{"claim": "dies", "question": "Does he die?", "answer": "Yes at 4s",
                                "supported": "yes"}],
                    "remove_tags": ["wrong"], "corrected_summary": "", "confidence": 0.9}
        return {"summary": "He dies to the boss", "category": "Fail", "secondary_categories": ["Rage"],
                "tags": ["boss", "death", "wrong"], "moments": [{"t": 4, "description": "dies"}],
                "on_screen": ["boss"], "mood": "Angry", "energy": 4, "profanity": True,
                "best_in": 1, "best_out": 9, "search_phrases": ["boss death"], "confidence": 0.8}

    def generate_text(self, system, prompt, images):
        return "He dies at (0:04)."


def test_full_pipeline_with_stub(monkeypatch, tmp_path):
    sid = add_streamer("alpha")
    add_clip(sid, "c1", "boss fight", duration=10)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpg")
    stub = StubProvider()
    monkeypatch.setattr(media, "download", lambda clip, login: video)
    monkeypatch.setattr(media, "extract_frames", lambda *a, **k: [{"path": str(frame), "t": 4.0}])
    monkeypatch.setattr(transcribe, "transcribe", lambda p: [{"start": 3.9, "end": 5, "text": "NO WAY"}])
    monkeypatch.setattr(analyze, "get_provider", lambda name=None: stub)

    steps = []
    analyze.run_pipeline("c1", progress=lambda p, m: steps.append(m))
    clip = analyze.get_clip("c1")
    assert clip["status"] == "done"
    assert clip["category"] == "Fail"
    assert clip["tags"] == ["boss", "death"]
    assert clip["needs_review"] == 0
    assert clip["transcript_text"] == "NO WAY"
    assert clip["file_path"] == str(video)
    checks = db.query("SELECT * FROM qa WHERE clip_id='c1' AND kind='verify'")
    assert checks[0]["supported"] == 1
    assert stub.calls == [("json", 1), ("json", 1)]
    assert steps[-1] == "Done"

    qa = analyze.ask("c1", "When does he die?")
    assert "0:04" in qa["answer"]

    fixed = analyze.correct("c1", summary="Actually he wins", tags=["Win"], category="Clutch / Highlight")
    assert fixed["summary"] == "Actually he wins" and fixed["tags"] == ["win"] and fixed["corrected"] == 1
    from app import search
    assert search.search("wins")["results"][0]["id"] == "c1"


def test_real_frame_extraction_with_ffmpeg(tmp_path):
    import subprocess
    exe = media.ffmpeg_exe()
    video = tmp_path / "test.mp4"
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=12:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-shortest", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    assert abs(media.probe_duration(video) - 12) < 0.2
    frames = media.extract_frames(video, "clip/with:odd chars", 6)
    assert [f["t"] for f in frames] == [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]
    assert all(Path(f["path"]).stat().st_size > 1000 for f in frames)


def test_pipeline_survives_missing_speech_model(monkeypatch, tmp_path):
    sid = add_streamer("alpha")
    add_clip(sid, "c2", "quiet", duration=10)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpg")
    stub = StubProvider()
    seen_prompts = []
    orig = stub.generate_json
    stub.generate_json = lambda s, p, i, sc: (seen_prompts.append(p), orig(s, p, i, sc))[1]
    monkeypatch.setattr(media, "download", lambda clip, login: video)
    monkeypatch.setattr(media, "extract_frames", lambda *a, **k: [{"path": str(frame), "t": 4.0}])

    def broken_load():
        raise OSError("403 Forbidden while downloading model")
    monkeypatch.setattr(transcribe, "_load", broken_load)
    monkeypatch.setattr(transcribe, "_load_error", None)
    monkeypatch.setattr(analyze, "get_provider", lambda name=None: stub)
    analyze.run_pipeline("c2")
    clip = analyze.get_clip("c2")
    assert clip["status"] == "done"
    assert clip["transcript"] is None
    assert "transcript unavailable" in seen_prompts[0]
    assert "403" in transcribe.last_error()
