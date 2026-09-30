"""Resolve script install, diagnostics, key tests and the Ollama endpoints."""
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import ollama_manager, resolve_setup

H = {"X-Clip-Manager": "1"}


@pytest.fixture()
def client():
    with TestClient(app, headers=H) as c:
        yield c


def test_resolve_script_install_and_status(tmp_path, monkeypatch, client):
    monkeypatch.setattr(resolve_setup, "scripts_dir", lambda: tmp_path / "Fusion" / "Scripts" / "Utility")
    s = client.get("/api/resolve/status").json()
    assert s["script_installed"] is False
    s = client.post("/api/resolve/install-script").json()
    assert s["script_installed"] and s["script_current"]
    installed = tmp_path / "Fusion" / "Scripts" / "Utility" / "Clip Manager.py"
    assert installed.read_bytes() == resolve_setup.SCRIPT_SOURCE.read_bytes()
    installed.write_text("old version")
    assert client.get("/api/resolve/status").json()["script_current"] is False


def test_resolve_test_reports_missing_resolve(client):
    r = client.post("/api/resolve/test").json()
    assert r["ok"] is False and r["message"]


def test_scripts_dir_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert resolve_setup.scripts_dir() == tmp_path / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"


def test_twitch_key_test(monkeypatch, client):
    from app import twitch

    def bad_token(self):
        raise twitch.TwitchError("Twitch rejected the Client ID/Secret (400). Check Settings.")
    monkeypatch.setattr(twitch.TwitchClient, "_get_token", bad_token)
    r = client.post("/api/setup/test-twitch", json={"client_id": "id", "client_secret": "s"}).json()
    assert r["ok"] is False and "rejected" in r["message"]
    monkeypatch.setattr(twitch.TwitchClient, "_get_token", lambda self: "tok")
    r = client.post("/api/setup/test-twitch", json={"client_id": "id", "client_secret": "s"}).json()
    assert r["ok"] is True
    r = client.post("/api/setup/test-twitch", json={}).json()  # nothing saved yet
    assert r["ok"] is False and "Client ID" in r["message"]


def test_claude_key_test(monkeypatch, client):
    import anthropic
    import httpx
    req = httpx.Request("GET", "https://api.anthropic.com/v1/models")

    class FakeModels:
        def __init__(self, err):
            self.err = err

        def list(self, limit):
            if self.err:
                raise self.err
            return []

    def make(err):
        return lambda **kw: types.SimpleNamespace(models=FakeModels(err))

    assert client.post("/api/setup/test-claude", json={}).json()["ok"] is False  # no key
    monkeypatch.setattr(anthropic, "Anthropic", make(anthropic.AuthenticationError(
        "bad", response=httpx.Response(401, request=req), body=None)))
    r = client.post("/api/setup/test-claude", json={"api_key": "sk-bad"}).json()
    assert r["ok"] is False and "rejected" in r["message"]
    monkeypatch.setattr(anthropic, "Anthropic", make(None))
    assert client.post("/api/setup/test-claude", json={"api_key": "sk-good"}).json()["ok"] is True


def test_setup_complete_flag(client):
    assert client.get("/api/settings").json()["settings"]["setup_complete"] is False
    client.post("/api/setup/complete", json={})
    assert client.get("/api/settings").json()["settings"]["setup_complete"] is True


def test_ollama_endpoints(monkeypatch, client, tmp_path):
    monkeypatch.setattr(ollama_manager, "running_models", lambda http=None: None)
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: None)
    monkeypatch.setattr(ollama_manager, "find_tray_app", lambda: None)
    monkeypatch.setattr(config.Settings, "model_fields", config.Settings.model_fields)
    config.save_settings({"ollama_model_dirs": [str(tmp_path / "nothing")]})
    s = client.get("/api/ollama/status").json()
    assert s["installed"] is False and s["running"] is False and "choice" in s and "pull" in s
    assert client.post("/api/ollama/start").status_code == 400
    assert client.post("/api/ollama/pull", json={}).status_code == 400
    monkeypatch.setattr(ollama_manager, "set_user_env", lambda *a, **k: "set")
    r = client.post("/api/ollama/use", json={"store": str(tmp_path), "model": "llava:7b"}).json()
    assert config.get_settings().ollama_model == "llava:7b"
    assert config.get_settings().ollama_models_dir == str(tmp_path)
    assert "set" in r["done"]


def test_diagnostics_report(client, tmp_path):
    config.save_settings({"twitch_client_secret": "supersecret", "anthropic_api_key": "sk-ant-secret"})
    (config.DATA_DIR / "app.log").write_text("line one\nBoom: something failed\n")
    text = client.get("/api/diagnostics").text
    assert "Clip Manager diagnostics" in text and "Boom: something failed" in text
    assert "supersecret" not in text and "sk-ant-secret" not in text
    assert "--- Offline AI (Ollama) ---" in text and "--- Resolve ---" in text


def test_setup_cli_ollama_not_installed(monkeypatch, capsys):
    from app import setup_cli
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: None)
    monkeypatch.setattr(ollama_manager, "find_tray_app", lambda: None)
    monkeypatch.setattr(ollama_manager, "running_models", lambda http=None: None)
    assert setup_cli.main(["ollama", "--yes"]) == 3
    assert "isn't installed" in capsys.readouterr().out


def test_setup_cli_ollama_uses_existing_model_without_download(monkeypatch, capsys, tmp_path):
    from app import setup_cli
    from test_ollama_setup import make_store
    store = make_store(tmp_path / "L" / ".ollama", {"qwen2.5vl:7b": {}}, nested="models")
    config.save_settings({"ollama_model_dirs": [str(tmp_path / "L" / ".ollama")]})
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: "/bin/ollama")
    monkeypatch.setattr(ollama_manager, "running_models", lambda http=None: {"qwen2.5vl:7b"})
    monkeypatch.setattr(ollama_manager, "set_user_env", lambda *a, **k: "Set OLLAMA_MODELS")
    pulled = []
    monkeypatch.setattr(ollama_manager, "pull", lambda m, http=None: pulled.append(m))
    assert setup_cli.main(["ollama", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "no download needed" in out and pulled == []
    assert config.get_settings().ollama_models_dir == str(store)


def test_setup_cli_restarts_mismatched_ollama(monkeypatch, capsys, tmp_path):
    from app import setup_cli
    from test_ollama_setup import make_store
    store = make_store(tmp_path / "L", {"qwen2.5vl:7b": {}})
    config.save_settings({"ollama_model_dirs": [str(tmp_path / "L")]})
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: "/bin/ollama")
    monkeypatch.setattr(ollama_manager, "running_models", lambda http=None: {"llama3:8b:latest"})
    monkeypatch.setattr(ollama_manager, "set_user_env", lambda *a, **k: "ok")
    restarted = []
    monkeypatch.setattr(ollama_manager, "restart", lambda d, http=None: restarted.append(d) or True)
    assert setup_cli.main(["ollama", "--yes"]) == 0
    assert restarted == [str(store)]
