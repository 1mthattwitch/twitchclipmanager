"""Regression tests from the final verification pass."""
import importlib.util
import subprocess
import types
from pathlib import Path

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, media, transcribe
from app.ai.base import AIError
from app.ai.claude import ClaudeProvider
from app.main import app
from conftest import add_clip, add_streamer

ROOT = Path(__file__).resolve().parents[2]


# ---------- only this computer can drive the app ----------

def test_actions_need_header_and_localhost():
    sid = add_streamer("alpha")
    add_clip(sid, "a1", "clip")
    with TestClient(app) as c:
        assert c.get("/api/clips").status_code == 200           # reading is fine
        assert c.post("/api/jobs/cancel-queued").status_code == 403  # a form POST from another site
        assert c.post("/api/clips/a1/analyze", json={}).status_code == 403
        ok = c.post("/api/jobs/cancel-queued", headers={"X-Clip-Manager": "1"})
        assert ok.status_code == 200
        for host in ("evil.example", "localhost.evil.example", "192.168.1.5:8765"):
            assert c.get("/api/settings", headers={"host": host}).status_code == 403, host
        for host in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765", "localhost"):
            assert c.get("/api/status", headers={"host": host}).status_code == 200, host


@pytest.mark.skipif(not (ROOT / "frontend" / "dist" / "index.html").exists(), reason="frontend not built")
def test_static_route_never_leaves_dist():
    config.save_settings({"twitch_client_secret": "topsecret"})
    with TestClient(app) as c:
        for path in ("/..%2f..%2fdata/settings.json", "/%2e%2e/%2e%2e/README.md", "/../../README.md",
                     "/..%5c..%5cREADME.md", "/assets/..%2f..%2f..%2fREADME.md"):
            r = c.get(path)
            assert "topsecret" not in r.text and "# Twitch Clip Manager" not in r.text, path


# ---------- Claude fallbacks never break analysis ----------

def _bad_request(msg):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


def _fake_client(beta_error, plain_result):
    calls = []

    def beta_create(**kw):
        calls.append("beta")
        raise beta_error

    def plain_create(**kw):
        calls.append("plain")
        if isinstance(plain_result, Exception):
            raise plain_result
        return plain_result

    ok = types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text='{"a": 1}')])
    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=beta_create)),
                                   messages=types.SimpleNamespace(create=plain_create))
    return client, calls, ok


@pytest.mark.parametrize("err", [_bad_request("unknown parameter"), TypeError("unexpected keyword 'fallbacks'")])
def test_claude_falls_back_to_plain_request(err):
    client, calls, ok = _fake_client(err, None)
    client.messages.create = lambda **kw: (calls.append("plain"), ok)[1]
    p = ClaudeProvider(client=client)
    assert p.generate_json("s", "p", [], {"type": "object"}) == {"a": 1}
    assert p.generate_json("s", "p", [], {"type": "object"}) == {"a": 1}
    assert calls == ["beta", "plain", "plain"]  # stops trying fallbacks after the first miss


def test_claude_real_errors_still_surface():
    client, calls, _ = _fake_client(_bad_request("bad image"), _bad_request("bad image"))
    with pytest.raises(AIError):
        ClaudeProvider(client=client).generate_json("s", "p", [], {"type": "object"})


# ---------- Whisper on Windows GPUs ----------

def test_broken_cuda_probe_means_cpu(monkeypatch):
    monkeypatch.setattr(transcribe, "_cuda_ok", {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0xC0000409))
    assert transcribe.cuda_works("small", 0) is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert transcribe.cuda_works("small", 1) is True


def test_cuda_runtime_error_falls_back_to_cpu(monkeypatch, tmp_path):
    loads = []

    class Model:
        def __init__(self, device):
            self.device = device

        def transcribe(self, *a, **k):
            if self.device == "cuda":
                raise RuntimeError("Could not load library cudnn_ops64_9.dll")
            return iter([types.SimpleNamespace(start=0.0, end=1.0, text=" hi ")]), None

    def fake_load():
        device = "cpu" if transcribe._cpu_only else "cuda"
        loads.append(device)
        transcribe._model_key = ("small", device, 0)
        return Model(device)

    monkeypatch.setattr(transcribe, "_load", fake_load)
    monkeypatch.setattr(transcribe, "_load_error", None)
    monkeypatch.setattr(transcribe, "_cpu_only", False)
    monkeypatch.setattr(transcribe, "decode_audio", lambda video: [0.0] * 16000)
    assert transcribe.transcribe(tmp_path / "x.mp4") == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert loads == ["cuda", "cpu"]


# ---------- Windows-hostile names and paths ----------

def test_windows_safe_filenames(tmp_path):
    lib = tmp_path / "My Clips – Ärger"
    config.save_settings({"library_dir": str(lib)})
    clip = {"id": "AbC-123_xyz", "created_at": "2025-03-01T00:00:00Z",
            "title": 'CON <what?> "he" said: a/b\\c|d*  ...  '}
    p = media.clip_path(clip, "streamer")
    name = p.name
    assert not any(ch in name for ch in '<>:"/\\|?*')
    assert name.endswith(".mp4") and not name.startswith("CON")
    assert p.parent.parent == lib
    empty = media.clip_path({"id": "x", "created_at": "", "title": "💀💀💀"}, "s")
    assert empty.name.endswith(".mp4") and "clip" in empty.name


def test_real_ffmpeg_with_spaces_and_unicode_in_path(tmp_path):
    folder = tmp_path / "Dossier vidéo ✂" / "sub dir"
    folder.mkdir(parents=True)
    video = folder / "clip ✂ 1.mp4"
    subprocess.run([media.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=3:size=320x240:rate=24", "-pix_fmt", "yuv420p", str(video)], check=True)
    frames = media.extract_frames(video, "clip id ✂", 3)
    assert len(frames) == 3


# ---------- the in-Resolve script ----------

def _load_resolve_script():
    spec = importlib.util.spec_from_file_location("cm_script", ROOT / "resolve" / "Clip Manager.py")
    src = spec.loader.get_source("cm_script").replace("\nmain()\n", "\n")
    mod = types.ModuleType("cm_script")
    exec(compile(src, "Clip Manager.py", "exec"), mod.__dict__)
    return mod


def test_resolve_script_finds_resolve_every_way():
    mod = _load_resolve_script()
    sentinel = object()
    mod.resolve = sentinel
    assert mod.connect_resolve() is sentinel
    del mod.resolve
    mod.bmd = types.SimpleNamespace(scriptapp=lambda name: sentinel if name == "Resolve" else None)
    assert mod.connect_resolve() is sentinel
    del mod.bmd
    mod.fusion = types.SimpleNamespace(GetResolve=lambda: sentinel)
    assert mod.connect_resolve() is sentinel
    del mod.fusion
    assert mod.connect_resolve() is None  # outside Resolve: clear message, no crash


def test_search_and_status_never_wait_for_model_download(monkeypatch):
    from app import db, embed, search
    monkeypatch.setattr(embed, "_failed", False)
    monkeypatch.setattr(embed, "_model", None)
    from app.bench_data import seed
    seed(add_streamer, add_clip)
    db.execute("UPDATE clips SET embedding=?", [b"\0" * 4 * 384])
    search.invalidate_cache()
    embed._lock.acquire()  # first-run download in progress
    try:
        res = search.search("snake")
        assert res["mode"] == "keyword" and res["results"][0]["id"] == "c29"
        assert embed.status() == "loading"
    finally:
        embed._lock.release()
