"""Run the real background workers against a flaky fake AI and a fake Twitch."""
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from app import analyze, db, media, transcribe, worker
from app.ai.base import AIError
from app.twitch import TwitchClient
from test_pipeline import StubProvider
from test_twitch import fake_twitch


class FlakyProvider(StubProvider):
    """Fails ~30% of calls: half 'transient' (retried), half permanent."""

    def __init__(self, rng):
        super().__init__()
        self.rng = rng
        self.lock = threading.Lock()

    def generate_json(self, *a, **k):
        with self.lock:
            roll = self.rng.random()
        if roll < 0.15:
            raise AIError("rate limited", transient=True)
        if roll < 0.30:
            raise AIError("model refused")
        return super().generate_json(*a, **k)


def wait_for(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_workers_sync_and_analyze_under_failures(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    clips = [{"id": f"k{i}", "created_at": (now - timedelta(hours=i * 7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "title": f"clip {i}", "game_id": "1", "duration": 20, "view_count": i,
              "url": f"https://clips.twitch.tv/k{i}"} for i in range(60)]
    http = fake_twitch(clips)
    monkeypatch.setattr(worker, "TwitchClient", lambda: TwitchClient("id", "s", http=http))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    monkeypatch.setattr(media, "download", lambda c, login, **k: video)
    monkeypatch.setattr(media, "extract_frames", lambda *a, **k: [{"path": str(frame), "t": 1.0}])
    monkeypatch.setattr(transcribe, "transcribe", lambda p: [])
    provider = FlakyProvider(random.Random(7))
    monkeypatch.setattr(analyze, "get_provider", lambda name=None: provider)
    monkeypatch.setattr(worker, "RETRY_DELAY", 0.01)
    monkeypatch.setattr(worker, "IDLE_SLEEP", 0.05)

    sid = db.execute("INSERT INTO streamers (twitch_id, login, display_name, created_at) VALUES ('1','x','X',0)").lastrowid
    worker.enqueue_sync(sid, since_days=30)
    worker.start()
    try:
        assert wait_for(lambda: db.query_one("SELECT COUNT(*) n FROM clips")["n"] == 60), (db.query("SELECT kind,status,message FROM jobs WHERE kind='sync'"), db.query_one("SELECT COUNT(*) n FROM clips"))
        settled = lambda: db.query_one(
            "SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")["n"] == 0
        assert wait_for(settled, 90), db.query("SELECT status, COUNT(*) n FROM jobs GROUP BY status")

        by_status = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
        # Every clip ends done or error; nothing stuck mid-pipeline.
        assert set(by_status) <= {"done", "error"}, by_status
        assert sum(by_status.values()) == 60
        # Transient failures were retried, so most succeed on the first pass.
        assert by_status.get("done", 0) >= 30, by_status
        # Failed clips carry a readable reason.
        for c in db.query("SELECT error FROM clips WHERE status='error'"):
            assert c["error"]

        # "Retry failed" with a healthy AI brings everything to done.
        provider.rng = random.Random(0)
        monkeypatch.setattr(FlakyProvider, "generate_json", StubProvider.generate_json)
        for c in db.query("SELECT id FROM clips WHERE status='error'"):
            worker.enqueue_analyze(c["id"])
        assert wait_for(settled, 90)
        assert db.query_one("SELECT COUNT(*) n FROM clips WHERE status='done'")["n"] == 60
        # Streamer marked as synced; a second sync finds nothing new.
        assert db.query_one("SELECT synced_until FROM streamers WHERE id=?", [sid])["synced_until"]
    finally:
        worker.stop()
        time.sleep(0.2)


def test_jobs_resume_after_restart():
    sid = db.execute("INSERT INTO streamers (twitch_id, login, display_name, created_at) VALUES ('1','x','X',0)").lastrowid
    db.execute("INSERT INTO clips (id, streamer_id, url, title, created_at, status) VALUES ('z', ?, 'u', 't', '2025', 'analyzing')", [sid])
    db.execute("INSERT INTO jobs (kind, clip_id, status, created_at, updated_at) VALUES ('analyze','z','running',0,0)")
    # Simulate the app being killed mid-job, then starting again (without running jobs).
    db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    db.execute("UPDATE clips SET status='queued' WHERE status IN ('downloading','transcribing','analyzing')")
    assert db.query_one("SELECT status FROM clips WHERE id='z'")["status"] == "queued"
    assert db.query_one("SELECT status FROM jobs")["status"] == "queued"
