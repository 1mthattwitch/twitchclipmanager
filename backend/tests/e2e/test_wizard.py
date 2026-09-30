"""Walk through the first-run setup wizard in a real browser (fake Twitch/Claude/Ollama)."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from test_ui import ROOT, SHOTS, free_port, launch  # noqa: E402


@pytest.fixture(scope="module")
def wizard_server(tmp_path_factory):
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("frontend not built")
    port = free_port()
    data = tmp_path_factory.mktemp("wizard")
    env = {k: v for k, v in os.environ.items() if k != "TCM_NO_WORKER"}
    env["TCM_DEMO_WIZARD"] = "1"
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("demo_server.py")), str(data), str(port)], env=env)
    import httpx
    url = f"http://127.0.0.1:{port}"
    for _ in range(300):
        try:
            if httpx.get(url + "/api/jobs", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("wizard demo server did not start")
    yield url, data
    proc.terminate()
    proc.wait(10)


def test_full_wizard(wizard_server):
    url, data = wizard_server
    with sync_playwright() as p:
        browser = launch(p)
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        wiz = pg.get_by_role("dialog", name="Setup")
        wiz.get_by_text("Welcome to Clip Manager").wait_for()
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(SHOTS / "10-setup-welcome.png"))
        wiz.get_by_role("button", name="Let's go").click()

        # Twitch: wrong secret is explained, right one passes
        wiz.get_by_label("Client ID").fill("abc123")
        wiz.get_by_label("Client Secret").fill("wrong")
        wiz.get_by_role("button", name="Test & save").click()
        expect(wiz.get_by_role("status")).to_contain_text("rejected")
        wiz.get_by_label("Client Secret").fill("good-secret")
        wiz.get_by_role("button", name="Test & save").click()
        expect(wiz.get_by_role("status")).to_contain_text("Twitch accepted your keys")
        wiz.get_by_role("button", name="Continue").click()

        # AI: finds both folders; qwen2.5vl is only in J, so J is picked; Ollama runs the wrong folder
        wiz.get_by_text("Your model folders").wait_for()
        expect(wiz).to_contain_text(".DoNotTouch")
        expect(wiz).to_contain_text("ollama_models")
        expect(wiz).to_contain_text("qwen2.5vl:7b is already in")
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(SHOTS / "11-setup-ai.png"))
        wiz.get_by_role("button", name="Use qwen2.5vl:7b from this folder").click()
        expect(wiz).to_contain_text("Set OLLAMA_MODELS=")
        wiz.get_by_role("button", name="Restart Ollama with").click()
        expect(wiz.get_by_role("status").last).to_contain_text("Offline AI is ready: qwen2.5vl:7b")
        # Optional Claude key for cross-checking
        wiz.get_by_label("Claude API key").fill("sk-ant-bad")
        wiz.get_by_role("button", name="Test & save").click()
        expect(wiz).to_contain_text("Claude rejected that key")
        wiz.get_by_label("Claude API key").fill("sk-ant-good")
        wiz.get_by_role("button", name="Test & save").click()
        expect(wiz).to_contain_text("Claude accepted your key")
        wiz.get_by_role("switch", name="Cross-check").click()
        wiz.get_by_role("button", name="Continue").click()

        # Resolve: install the script
        wiz.get_by_role("button", name="Install script").click()
        expect(wiz.get_by_role("button", name="Reinstall script")).to_be_visible()
        assert (data / "ResolveScripts" / "Clip Manager.py").exists()
        wiz.get_by_role("button", name="Test connection").click()
        expect(wiz.get_by_role("status")).to_be_visible()
        wiz.get_by_role("button", name="Skip for now").click()

        # First streamer
        wiz.get_by_label("Streamer name").fill("somestreamer")
        wiz.get_by_role("button", name="Add", exact=True).click()
        expect(wiz).to_contain_text("Added Somestreamer")
        wiz.get_by_role("button", name="Finish").click()
        expect(pg.get_by_role("dialog", name="Setup")).to_have_count(0)

        # Everything was saved
        import httpx
        s = httpx.get(url + "/api/settings").json()["settings"]
        assert s["setup_complete"] is True and s["ollama_model"] == "qwen2.5vl:7b"
        assert s["ollama_models_dir"].endswith("ollama_models") and s["claude_cross_check"] is True
        assert s["twitch_client_id"] == "abc123"

        # Reload: wizard stays closed; Settings can reopen it and copy diagnostics
        pg.reload()
        pg.get_by_role("button", name="Settings").first.click()
        pg.get_by_text("Setup & help").wait_for()
        pg.context.grant_permissions(["clipboard-read", "clipboard-write"])
        pg.get_by_role("button", name="Copy diagnostics").click()
        pg.get_by_text("Diagnostics copied").wait_for()
        clip = pg.evaluate("navigator.clipboard.readText()")
        assert "Clip Manager diagnostics" in clip and "good-secret" not in clip and "sk-ant-good" not in clip
        pg.get_by_role("button", name="Run the setup wizard").click()
        pg.get_by_role("dialog", name="Setup").get_by_text("Connect Twitch").wait_for()
        browser.close()
        assert not errors, errors
