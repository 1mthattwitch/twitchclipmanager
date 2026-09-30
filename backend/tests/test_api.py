from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from conftest import add_clip, add_streamer


def test_settings_masks_secrets():
    with TestClient(app, headers={"X-Clip-Manager": "1"}) as c:
        c.put("/api/settings", json={"twitch_client_secret": "supersecret1234", "ai_mode": "claude"})
        s = c.get("/api/settings").json()["settings"]
        assert s["twitch_client_secret"] == "••••1234"
        assert s["ai_mode"] == "claude"
        # Sending the masked value back must not overwrite the real secret.
        c.put("/api/settings", json={"twitch_client_secret": "••••1234"})
        assert config.get_settings().twitch_client_secret == "supersecret1234"


def test_clip_endpoints(tmp_path, monkeypatch):
    sid = add_streamer("alpha")
    add_clip(sid, "a1", "huge clutch", summary="wins the round", category="Clutch / Highlight", status="done")
    monkeypatch.setattr("app.main._fetch_files", lambda ids: None)
    with TestClient(app, headers={"X-Clip-Manager": "1"}) as c:
        res = c.get("/api/clips", params={"q": "clutch"}).json()
        assert res["results"][0]["id"] == "a1"
        clip = c.get("/api/clips/a1").json()
        assert clip["qa"] == [] and clip["has_file"] is False
        assert c.patch("/api/clips/a1", json={"starred": True}).json()["starred"] == 1
        assert c.get("/api/clips", params={"starred": True}).json()["total"] == 1
        c.post("/api/resolve/queue", json={"ids": ["a1"]})
        video = tmp_path / "a1.mp4"
        video.write_bytes(b"x")  # downloaded in the background by then
        db.update("clips", "id", "a1", file_path=str(video))
        q = c.get("/api/resolve/queue").json()
        assert q["items"][0]["clip_id"] == "a1"
        c.post("/api/resolve/queue/ack", json={"ids": [q["items"][0]["queue_id"]]})
        assert c.get("/api/resolve/queue").json()["items"] == []
        assert "def push_clips" in c.get("/api/resolve/bridge.py").text
        assert c.get("/api/streamers").json()[0]["clip_count"] == 1
