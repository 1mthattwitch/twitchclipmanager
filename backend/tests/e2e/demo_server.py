"""Starts the real app on a throwaway data dir with generated videos and a scripted fake AI.

Used by the browser end-to-end test. Usage: python tests/e2e/demo_server.py <data_dir> <port>
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[1]))  # tests/ for fixtures
data_dir = Path(sys.argv[1])
port = int(sys.argv[2])
os.environ["TCM_DATA_DIR"] = str(data_dir)

import faulthandler  # noqa: E402
import signal  # noqa: E402

import uvicorn  # noqa: E402

faulthandler.register(signal.SIGUSR1)

from app import analyze, config, db, media  # noqa: E402
from app.ai import base  # noqa: E402
from app.bench_data import CLIPS  # noqa: E402


class FakeAI(base.Provider):
    name = "local"
    label = "fake-ai"

    def available(self):
        return True, "ok"

    def generate_json(self, system, prompt, images, schema):
        if "checks" in schema["properties"]:
            return {"checks": [{"claim": "c", "question": "Is the streamer on screen?", "answer": "Yes, in every frame.", "supported": "yes"},
                               {"claim": "c", "question": "Does anyone shout?", "answer": "Unclear from the audio.", "supported": "unsure"}],
                    "remove_tags": [], "corrected_summary": "", "confidence": 0.9}
        return {"summary": "A freshly analysed test clip with colour bars.", "category": "Funny", "secondary_categories": [],
                "tags": ["test", "colour bars"], "moments": [{"t": 1, "description": "bars appear"}], "on_screen": [],
                "mood": "calm", "energy": 2, "profanity": False, "best_in": 0, "best_out": 3,
                "search_phrases": ["colour bars"], "confidence": 0.95}

    def generate_text(self, system, prompt, images):
        return "The bars change colour at (0:02)."


base.get_provider = lambda name=None: FakeAI()
analyze.get_provider = base.get_provider

WIZARD = os.environ.get("TCM_DEMO_WIZARD") == "1"
config.save_settings({"library_dir": str(data_dir / "library"), "twitch_client_id": "" if WIZARD else "demo",
                      "twitch_client_secret": "" if WIZARD else "demo", "background_recheck": False,
                      "setup_complete": not WIZARD})

if WIZARD:
    # A fresh install: fake Twitch/Claude answers, two model folders like the user's, a fake Ollama.
    import anthropic
    import types
    from app import twitch
    from app.services import ollama_manager, resolve_setup
    from test_ollama_setup import make_store
    L = data_dir / "L" / ".DoNotTouch" / "models" / ".ollama"
    J = data_dir / "J" / "ai" / "ollama_models"
    make_store(L, {"llama3:8b": {"size": 4_700_000_000}}, nested="models")
    make_store(J, {"qwen2.5vl:7b": {"size": 6_000_000_000}, "mistral:7b": {"size": 4_100_000_000}})
    config.save_settings({"ollama_model_dirs": [str(L), str(J)]})
    twitch.TwitchClient._get_token = lambda self: (_ for _ in ()).throw(
        twitch.TwitchError("Twitch rejected the Client ID/Secret (400). Check Settings.")) if self.client_secret != "good-secret" else "tok"
    twitch.TwitchClient.get_user = lambda self, login: {"id": "99", "login": login, "display_name": login.title(),
                                                        "profile_image_url": None}
    class _Models:
        def __init__(self, key): self.key = key
        def list(self, limit):
            if self.key != "sk-ant-good":
                import httpx
                raise anthropic.AuthenticationError("bad", response=httpx.Response(401, request=httpx.Request("GET", "https://x")), body=None)
            return []
    anthropic.Anthropic = lambda **kw: types.SimpleNamespace(models=_Models(kw.get("api_key")))
    state = {"running": {"llama3:8b"}}          # Ollama running with the wrong folder
    ollama_manager.find_exe = lambda: "C:/fake/ollama.exe"
    ollama_manager.running_models = lambda http=None: set(state["running"]) if state["running"] is not None else None
    def _restart(store, http=None):
        from app.services import ollama_stores
        state["running"] = ollama_stores.store_model_names(store)
        return True
    ollama_manager.restart = _restart
    ollama_manager.set_user_env = lambda name, value, **k: f"Set {name}={value} for your Windows user"
    resolve_setup.scripts_dir = lambda: data_dir / "ResolveScripts"
    resolve_setup.resolve_installed = lambda: True
db.connect()
ffmpeg = media.ffmpeg_exe()
sid = db.execute("INSERT INTO streamers (twitch_id, login, display_name, created_at, synced_until) VALUES ('1','demo','DemoStreamer',0,'2025-06-01T00:00:00Z')").lastrowid
sid2 = db.execute("INSERT INTO streamers (twitch_id, login, display_name, created_at) VALUES ('2','other','OtherStreamer',0)").lastrowid
lib = data_dir / "library"
lib.mkdir(parents=True, exist_ok=True)
for i, (cid, title, cat, game, summary, tags, moments, phrases) in enumerate(CLIPS):
    video = lib / f"{cid}.mp4"
    hue = (i * 37) % 360
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=duration=4:size=640x360:rate=24,hue=h={hue}",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    analysed = i < 26
    frames = media.extract_frames(video, cid, 3)
    analysis = {"summary": summary, "category": cat, "tags": tags, "search_phrases": phrases, "secondary_categories": [],
                "moments": [{"t": min(t, 3.5), "description": d} for t, d in moments], "mood": "hyped", "energy": 3 + i % 3,
                "profanity": i % 4 == 0, "best_in": 0.5, "best_out": 3.5, "on_screen": [], "confidence": 0.9}
    db.execute(
        """INSERT INTO clips (id, streamer_id, url, title, creator_name, game_name, view_count, created_at, duration,
           thumbnail_url, status, file_path, frames, transcript, transcript_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [cid, sid if i % 5 else sid2, f"https://clips.twitch.tv/{cid}", title, "viewer123", game, 5000 - i * 150,
         f"2025-0{1 + i % 9}-1{i % 10}T12:00:00Z", 4, f"/media/frame/{cid}/0", "done" if analysed else "new",
         str(video), json.dumps(frames), json.dumps([{"start": 0.5, "end": 2, "text": summary}]), summary])
    if analysed:
        db.update("clips", "id", cid, analysis=analysis, summary=summary, category=cat, tags=tags, mood="hyped",
                  energy=analysis["energy"], confidence=0.55 if i == 3 else 0.9, needs_review=int(i == 3),
                  provider="fake-ai", analyzed_at=1)
        db.execute("INSERT INTO qa (clip_id, kind, question, answer, supported, provider, created_at) VALUES (?,?,?,?,?,?,?)",
                   [cid, "verify", "Is this what happens?", "Yes, the frames show it.", 1, "fake-ai", 1])
    db.reindex_clip(cid)

from app.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
