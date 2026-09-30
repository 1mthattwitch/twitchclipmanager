"""Clip downloads (yt-dlp + Twitch fallback), videos not kept, auto-pause, queue info."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import __main__ as entry
from app import analyze, config, db, media, transcribe, worker
from app.main import app
from conftest import add_clip, add_streamer
from test_pipeline import StubProvider

CLIP = {"id": "BraveTinyOtterKappa-AbC123xyz_Q", "title": "big play", "created_at": "2025-03-01T10:00:00Z",
        "url": "https://clips.twitch.tv/BraveTinyOtterKappa-AbC123xyz_Q"}
VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"v" * 5000


def twitch(gql=None, video_status=200, seen=None):
    gql = gql if gql is not None else [{"data": {"clip": {
        "playbackAccessToken": {"signature": "SIG", "value": '{"clip":1}'},
        "videoQualities": [
            {"quality": "360", "frameRate": 30, "sourceURL": "https://cdn.test/360.mp4"},
            {"quality": "1080", "frameRate": 60, "sourceURL": "https://cdn.test/1080.mp4"},
            {"quality": "720", "frameRate": 60, "sourceURL": "https://cdn.test/720.mp4"},
        ]}}}]

    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        if req.url.host == "gql.twitch.tv":
            return httpx.Response(200, json=gql) if isinstance(gql, list) else httpx.Response(gql)
        return httpx.Response(video_status, content=VIDEO if video_status == 200 else b"no")
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def ytdlp_broken(monkeypatch):
    def fail(clip, target):
        raise RuntimeError("ERROR: [twitch:clips] BraveTinyOtterKappa-AbC123xyz_Q: Unable to download JSON metadata: HTTP Error 404\nmore")
    monkeypatch.setattr(media, "_download_ytdlp", fail)
    upgrades = []
    monkeypatch.setattr(media, "_upgrade_ytdlp_in_background", lambda: upgrades.append(1))
    return upgrades


def test_fallback_downloads_best_quality_when_ytdlp_fails(ytdlp_broken):
    seen = []
    path = media.download(CLIP, "streamer", http=twitch(seen=seen))
    assert path.read_bytes() == VIDEO and path.suffix == ".mp4"
    assert not list(path.parent.glob("*.part"))
    gql = json.loads(seen[0].content)[0]
    assert gql["variables"] == {"slug": CLIP["id"]} and seen[0].headers["Client-ID"] == media.TWITCH_WEB_CLIENT_ID
    video_req = seen[1].url
    assert video_req.path == "/1080.mp4" and video_req.params["sig"] == "SIG" and video_req.params["token"] == '{"clip":1}'
    assert ytdlp_broken == [1]  # a newer yt-dlp is fetched for next time


def test_deleted_clip_is_a_per_clip_error(ytdlp_broken):
    with pytest.raises(media.ClipGone, match="deleted"):
        media.download(CLIP, "streamer", http=twitch(gql=[{"data": {"clip": None}}]))


def test_both_methods_failing_reports_both_reasons(ytdlp_broken):
    with pytest.raises(media.MediaError) as e:
        media.download(CLIP, "streamer", http=twitch(gql=403))
    msg = str(e.value)
    assert "yt-dlp: [twitch:clips]" in msg and "HTTP Error 404" in msg and "Twitch answered 403" in msg
    assert "more" not in msg  # first line only


def test_video_server_error_leaves_no_partial_file(ytdlp_broken):
    with pytest.raises(media.MediaError, match="video server answered 403"):
        media.download(CLIP, "streamer", http=twitch(video_status=403))
    lib = config.get_settings().library_dir
    import pathlib
    assert not [p for p in pathlib.Path(lib).rglob("*") if p.is_file()]


def test_ytdlp_success_skips_fallback(monkeypatch):
    monkeypatch.setattr(media, "_download_ytdlp", lambda clip, target: target.write_bytes(VIDEO))
    boom = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("fallback used")))
    assert media.download(CLIP, "streamer", http=boom).read_bytes() == VIDEO


# ---------- videos are not kept after analysis ----------

def _pipeline(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")

    def fake_download(clip, login, **k):
        video.write_bytes(VIDEO)
        return video
    monkeypatch.setattr(media, "download", fake_download)
    monkeypatch.setattr(media, "extract_frames", lambda *a, **k: [{"path": str(frame), "t": 1.0}])
    monkeypatch.setattr(transcribe, "transcribe", lambda p: [])
    monkeypatch.setattr(analyze, "get_provider", lambda name=None: StubProvider())
    sid = add_streamer("s")
    add_clip(sid, "c1", "a clip")
    return video


def test_video_deleted_after_analysis_by_default(monkeypatch, tmp_path):
    video = _pipeline(monkeypatch, tmp_path)
    analyze.run_pipeline("c1")
    clip = analyze.get_clip("c1")
    assert clip["status"] == "done" and clip["summary"] and clip["frames"]
    assert not video.exists() and clip["file_path"] is None


def test_keep_videos_setting_keeps_them(monkeypatch, tmp_path):
    video = _pipeline(monkeypatch, tmp_path)
    config.save_settings({"keep_videos": True})
    analyze.run_pipeline("c1")
    assert video.exists() and analyze.get_clip("c1")["file_path"] == str(video)


def test_resolve_and_show_file_download_on_demand(monkeypatch, tmp_path):
    video = _pipeline(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr("app.main.subprocess.Popen", lambda *a, **k: opened.append(a))
    with TestClient(app, headers={"X-Clip-Manager": "1"}) as c:
        assert c.get("/api/clips/c1").json()["has_file"] is False
        assert c.post("/api/clips/c1/reveal").status_code == 200
        assert video.exists() and c.get("/api/clips/c1").json()["has_file"] is True
        assert opened


def test_resolve_queue_waits_for_download(monkeypatch, tmp_path):
    _pipeline(monkeypatch, tmp_path)
    ran = []
    monkeypatch.setattr("app.main._fetch_files", lambda ids: ran.append(ids))
    with TestClient(app, headers={"X-Clip-Manager": "1"}) as c:
        c.post("/api/resolve/queue", json={"ids": ["c1"]})
        q = c.get("/api/resolve/queue").json()
        assert q["items"] == [] and q["waiting"] == 1
        import app.main as main_mod
        main_mod._ensure_file(analyze.get_clip("c1"))
        q = c.get("/api/resolve/queue").json()
        assert [i["clip_id"] for i in q["items"]] == ["c1"] and q["waiting"] == 0


# ---------- failure reasons, auto-pause ----------

def test_normalize_reason_groups_same_problem_across_clips():
    a = worker.normalize_reason("Download failed. yt-dlp: [twitch:clips] FunnyCatClip-Xy12ab34CD: HTTP Error 404 (caused by https://gql.twitch.tv/gql). Direct: Twitch answered 403")
    b = worker.normalize_reason("Download failed. yt-dlp: [twitch:clips] OtherClipName-Zz98yy76QQ: HTTP Error 404 (caused by https://gql.twitch.tv/gql). Direct: Twitch answered 403")
    assert a == b and "403" in a and "404" in a
    assert worker.normalize_reason("Ollama isn't running (retry 2/2)") == "Ollama isn't running"
    assert worker.normalize_reason("Model qwen2.5vl:7b isn't downloaded") == "Model qwen2.5vl:7b isn't downloaded"
    assert worker.normalize_reason(None) == "Unknown error"


def _job(i):
    return {"id": i, "kind": "analyze", "clip_id": f"c{i}"}


def test_five_identical_failures_pause_analysis():
    worker.resume()
    for i in range(worker.PAUSE_AFTER - 1):
        worker._note_result(_job(i), media.MediaError(f"Download failed. Direct: clip Abcdefghij{i}xyz12 answered 403"))
    assert not worker.paused()
    worker._note_result(_job(9), media.MediaError("Download failed. Direct: clip Zyxwvutsrq9xyz12 answered 403"))
    p = worker.paused()
    assert p["auto"] and "answered 403" in p["reason"]
    worker.resume()
    assert not worker.paused() and worker._streak["n"] == 0


def test_mixed_failures_deleted_clips_and_successes_do_not_pause():
    worker.resume()
    for i in range(20):
        worker._note_result(_job(i), media.ClipGone("This clip was deleted"))
    for i in range(3):
        worker._note_result(_job(i), RuntimeError("A"))
    worker._note_result(_job(5), None)
    for i in range(3):
        worker._note_result(_job(i), RuntimeError("A"))
    worker._note_result(_job(6), RuntimeError("B"))
    worker._note_result({"id": 7, "kind": "sync", "clip_id": None}, RuntimeError("A"))
    assert not worker.paused()


def test_paused_gpu_lane_leaves_jobs_queued(monkeypatch):
    sid = add_streamer("p")
    add_clip(sid, "q1", "one")
    worker.enqueue_analyze("q1")
    worker.pause()
    calls = []
    monkeypatch.setattr(worker, "_next_job", lambda kinds: calls.append(kinds))
    monkeypatch.setattr(worker, "IDLE_SLEEP", 0.01)
    import threading
    t = threading.Thread(target=worker._loop, args=("gpu",), daemon=True)
    worker._stop.clear()
    t.start()
    t.join(0.2)
    worker._stop.set()
    worker._wake.set()
    t.join(2)
    assert calls == []
    assert db.query_one("SELECT status FROM jobs WHERE clip_id='q1'")["status"] == "queued"
    worker.resume()


def test_queue_endpoints_pause_resume_groups_and_estimate():
    sid = add_streamer("e")
    for i in range(3):
        add_clip(sid, f"e{i}", f"clip {i}")
        worker.enqueue_analyze(f"e{i}")
    for i, msg in enumerate(["Ollama isn't running", "Ollama isn't running (retry 1/2)", "Download failed. Direct: Twitch answered 403"]):
        db.execute("INSERT INTO jobs (kind, clip_id, params, status, message, created_at, updated_at) VALUES ('analyze',?, '{}', 'error', ?, 0, 0)",
                   [f"x{i}", msg])
    worker._durations.clear()
    worker._durations.extend([30.0, 90.0])
    with TestClient(app, headers={"X-Clip-Manager": "1"}) as c:
        r = c.get("/api/jobs").json()
        assert r["paused"] is None
        assert r["error_groups"][0] == {"reason": "Ollama isn't running", "count": 2}
        assert r["eta_seconds"] == 180.0  # 3 waiting x 60 s average
        assert c.post("/api/jobs/pause").status_code == 200
        assert c.get("/api/jobs").json()["paused"]["reason"] == "Paused by you"
        c.post("/api/jobs/resume")
        assert c.get("/api/jobs").json()["paused"] is None
        worker.pause("Ollama isn't running", auto=True)
        c.post("/api/jobs/retry-failed")  # retrying also resumes
        assert c.get("/api/jobs").json()["paused"] is None
    worker._durations.clear()


# ---------- the chat tab on start ----------

def test_start_opens_chat_then_app(monkeypatch):
    monkeypatch.delenv("TCM_NO_BROWSER", raising=False)
    opened = []
    urls = entry.open_tabs(opened.append)
    assert opened == urls and urls[0].startswith("https://claude.ai/code/session_") and urls[1].startswith("http://localhost:")


def test_chat_tab_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("TCM_NO_BROWSER", raising=False)
    config.save_settings({"open_chat_on_start": False})
    opened = []
    entry.open_tabs(opened.append)
    assert len(opened) == 1 and opened[0].startswith("http://localhost:")


def test_no_browser_opens_nothing(monkeypatch):
    monkeypatch.setenv("TCM_NO_BROWSER", "1")
    opened = []
    assert entry.open_tabs(opened.append) == [] and opened == []


def test_video_deleted_even_when_transcription_crashes(monkeypatch, tmp_path):
    video = _pipeline(monkeypatch, tmp_path)

    def boom(p):
        raise RuntimeError("whisper crashed")
    monkeypatch.setattr(transcribe, "transcribe", boom)
    with pytest.raises(RuntimeError):
        analyze.run_pipeline("c1")
    assert not video.exists() and analyze.get_clip("c1")["file_path"] is None


def test_sorting_thousands_of_clips_leaves_no_videos_on_disk(monkeypatch, tmp_path):
    """The real queue, the real download function (Twitch faked): nothing piles up."""
    import threading
    from pathlib import Path
    written = []

    def fake_ytdlp(clip, target):
        target.write_bytes(VIDEO)
        written.append(target)
    monkeypatch.setattr(media, "_download_ytdlp", fake_ytdlp)
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    monkeypatch.setattr(media, "extract_frames", lambda *a, **k: [{"path": str(frame), "t": 1.0}])
    monkeypatch.setattr(transcribe, "transcribe", lambda p: [])
    monkeypatch.setattr(analyze, "get_provider", lambda name=None: StubProvider())
    monkeypatch.setattr(worker, "IDLE_SLEEP", 0.02)
    sid = add_streamer("big")
    n = 40
    for i in range(n):
        add_clip(sid, f"Clip{i:03d}", f"clip {i}")
        worker.enqueue_analyze(f"Clip{i:03d}")
    worker._stop.clear()
    t = threading.Thread(target=worker._loop, args=("gpu",), daemon=True)
    t.start()
    import time
    end = time.time() + 60
    while time.time() < end and db.query_one("SELECT COUNT(*) AS n FROM clips WHERE status='done'")["n"] < n:
        time.sleep(0.05)
    worker._stop.set()
    worker._wake.set()
    t.join(5)
    assert db.query_one("SELECT COUNT(*) AS n FROM clips WHERE status='done'")["n"] == n
    assert len(written) == n and all(p.parent == media.temp_dir() for p in written)
    lib = Path(config.get_settings().library_dir)
    assert not lib.exists() or not [p for p in lib.rglob("*") if p.is_file()]  # library untouched
    assert not [p for p in media.temp_dir().iterdir()]  # every video deleted
    assert db.query_one("SELECT COUNT(*) AS n FROM clips WHERE file_path IS NOT NULL")["n"] == 0


def test_leftover_videos_are_removed_on_start():
    media.temp_dir().mkdir(parents=True, exist_ok=True)
    (media.temp_dir() / "abc.mp4").write_bytes(VIDEO)
    (media.temp_dir() / "abc.mp4.part").write_bytes(VIDEO)
    with TestClient(app, headers={"X-Clip-Manager": "1"}):
        assert list(media.temp_dir().iterdir()) == []


# ---------- speech-to-text doesn't depend on PyAV ----------

def _ffmpeg_clip(path, audio: bool):
    import subprocess
    ff = media.ffmpeg_exe()
    if not ff:
        pytest.skip("no ffmpeg")
    args = [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=2:size=160x90:rate=10"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest"]
    subprocess.run(args + ["-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_audio_decoded_by_ffmpeg(tmp_path):
    a = transcribe.decode_audio(_ffmpeg_clip(tmp_path / "a.mp4", audio=True))
    assert a.dtype.name == "float32" and 28000 < a.size < 36000  # ~2 s at 16 kHz
    assert transcribe.decode_audio(_ffmpeg_clip(tmp_path / "silent.mp4", audio=False)) is None


class _FakeWhisper:
    def __init__(self, error=None):
        self.error, self.got = error, None

    def transcribe(self, audio, **k):
        if self.error:
            raise self.error
        self.got = audio
        seg = type("S", (), {"start": 0.0, "end": 1.0, "text": " hello "})
        return iter([seg]), None


def test_whisper_gets_samples_not_a_file(monkeypatch, tmp_path):
    model = _FakeWhisper()
    monkeypatch.setattr(transcribe, "_load", lambda: model)
    monkeypatch.setattr(transcribe, "_load_error", None)
    out = transcribe.transcribe(_ffmpeg_clip(tmp_path / "a.mp4", audio=True))
    assert out == [{"start": 0.0, "end": 1.0, "text": "hello"}] and not isinstance(model.got, str)
    assert transcribe.transcribe(_ffmpeg_clip(tmp_path / "s.mp4", audio=False)) == []


def test_speech_error_falls_back_to_frames_instead_of_failing_the_clip(monkeypatch, tmp_path):
    model = _FakeWhisper(TypeError("open() got an unexpected keyword argument 'metadata_errors'"))
    monkeypatch.setattr(transcribe, "_load", lambda: model)
    monkeypatch.setattr(transcribe, "_load_error", None)
    assert transcribe.transcribe(_ffmpeg_clip(tmp_path / "a.mp4", audio=True)) is None
    assert "metadata_errors" in transcribe.last_error()
    monkeypatch.setattr(transcribe, "_load_error", None)
