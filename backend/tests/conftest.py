import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["TCM_NO_WORKER"] = "1"

from app import config, db, embed, search  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "data" / "settings.json")
    monkeypatch.setattr(config, "_cached", None)
    for var in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    config.save_settings({"library_dir": str(tmp_path / "library")})
    db.reset_for_tests()
    search.invalidate_cache()
    # No model downloads in tests: embeddings are off unless a test fakes them.
    monkeypatch.setattr(embed, "embed", lambda texts, query=False: None)
    yield
    db.reset_for_tests()


def add_streamer(login="streamer"):
    cur = db.execute(
        "INSERT INTO streamers (twitch_id, login, display_name, created_at) VALUES (?,?,?,?)",
        [f"id-{login}", login, login.title(), db.now()])
    return cur.lastrowid


def add_clip(streamer_id, clip_id, title, **fields):
    db.execute(
        "INSERT INTO clips (id, streamer_id, url, title, created_at, duration, view_count) VALUES (?,?,?,?,?,?,?)",
        [clip_id, streamer_id, f"https://clips.twitch.tv/{clip_id}", title,
         fields.pop("created_at", "2025-01-01T00:00:00Z"), fields.pop("duration", 30),
         fields.pop("view_count", 10)])
    if fields:
        db.update("clips", "id", clip_id, **fields)
    db.reindex_clip(clip_id)
