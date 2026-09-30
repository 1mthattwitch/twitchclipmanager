"""Browser end-to-end test. Needs a built frontend (npm run build) and Playwright.

Run: pytest tests/e2e -s
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402
ROOT = Path(__file__).resolve().parents[3]
SHOTS = Path(os.environ.get("TCM_SCREENSHOTS", ROOT / "docs" / "screenshots"))


def launch(p):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
    return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("frontend not built")
    port = free_port()
    data = tmp_path_factory.mktemp("e2e")
    global DATA
    DATA = data
    env = {k: v for k, v in os.environ.items() if k != "TCM_NO_WORKER"}  # the demo needs its worker
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("demo_server.py")), str(data), str(port)], env=env)
    url = f"http://127.0.0.1:{port}"
    import httpx
    for _ in range(200):
        try:
            if httpx.get(url + "/api/status", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("demo server did not start")
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture()
def page(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch(p)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        pg = ctx.new_page()
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "clips.twitch.tv" not in m.text else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        pg.goto(server)
        yield pg
        browser.close()
        assert not errors, errors


def shot(pg, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.wait_for_timeout(400)
    pg.screenshot(path=str(SHOTS / f"{name}.png"))


def test_library_search_and_sheet(page):
    pg = page
    pg.get_by_test_id("clip-card").first.wait_for()
    assert pg.get_by_test_id("clip-card").count() == 30
    shot(pg, "01-library")

    # Search by meaning of the AI description (keyword fallback in this sandbox)
    pg.get_by_label("Search clips").fill("falls off his chair")
    pg.wait_for_timeout(700)
    first = pg.get_by_test_id("clip-card").first
    assert "WHAT" in first.inner_text()
    shot(pg, "02-search")

    # Category chip filter
    pg.get_by_label("Search clips").fill("")
    pg.get_by_role("button", name="Fail", exact=False).first.click()
    pg.wait_for_timeout(600)
    texts = [c.inner_text() for c in pg.get_by_test_id("clip-card").all()]
    assert texts and all("Fail" in t for t in texts)
    pg.get_by_role("button", name="All", exact=True).click()

    # Open the clip sheet
    pg.get_by_label("Search clips").fill("smoke alarm")
    pg.wait_for_timeout(700)
    pg.get_by_test_id("clip-card").first.click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_role("heading", name="What happens").wait_for()
    assert "smoke alarm" in dialog.inner_text().lower()
    assert "how the ai double-checked itself" in dialog.inner_text().lower()
    shot(pg, "03-clip-sheet")

    # Ask a question
    dialog.get_by_placeholder("e.g. What does he say after he dies?").fill("When do the bars change?")
    dialog.get_by_role("button", name="Ask").click()
    dialog.get_by_text("The bars change colour at (0:02).").wait_for()

    # Correct the AI
    dialog.get_by_role("button", name="Correct").click()
    dialog.get_by_label("Summary").fill("The pancakes catch fire and the whole kitchen fills with smoke.")
    dialog.get_by_label("Tags").fill("fire, pancakes, smoke")
    dialog.get_by_role("button", name="Save").click()
    dialog.get_by_text("The pancakes catch fire").wait_for()
    expect(dialog.get_by_text("Corrected", exact=True)).to_be_visible()
    expect(dialog).to_contain_text("corrected by you")

    # Star, then close with Escape
    dialog.get_by_label("Star").click()
    pg.wait_for_timeout(300)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(500)
    assert pg.get_by_role("dialog").count() == 0

    # The correction is searchable immediately
    pg.get_by_label("Search clips").fill("catch fire")
    pg.wait_for_timeout(700)
    assert pg.get_by_test_id("clip-card").count() == 1


def test_analyse_flow_updates_live(page, server):
    pg = page
    pg.get_by_test_id("clip-card").first.wait_for()
    pg.get_by_label("Search clips").fill("parents")
    pg.wait_for_timeout(700)
    pg.get_by_test_id("clip-card").first.click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_text("Not analysed yet").wait_for()
    dialog.get_by_role("button", name="Analyse").click()
    try:
        expect(dialog).to_contain_text("A freshly analysed test clip", timeout=30000)
    except AssertionError:
        import httpx
        print("JOBS:", httpx.get(server + "/api/jobs").text[:800])
        raise
    assert "Is the streamer on screen?" in dialog.inner_text()


def test_filters_select_and_other_tabs(page):
    pg = page
    pg.get_by_test_id("clip-card").first.wait_for()
    pg.get_by_label("Filters").click()
    fd = pg.get_by_role("dialog", name="Filters")
    fd.get_by_role("switch", name="Only clips that need a look").click()
    fd.get_by_role("button", name="Done").click()
    pg.wait_for_timeout(700)
    assert pg.get_by_test_id("clip-card").count() == 1

    pg.get_by_label("Filters").click()
    pg.get_by_role("dialog", name="Filters").get_by_role("button", name="Reset").click()
    pg.get_by_role("dialog", name="Filters").get_by_role("button", name="Done").click()

    pg.get_by_role("button", name="Select").click()
    cards = pg.get_by_test_id("clip-card")
    cards.nth(0).click()
    cards.nth(1).click()
    pg.get_by_text("2 selected").wait_for()
    shot(pg, "04-select")
    pg.get_by_role("button", name="Queue for Resolve").click()
    pg.get_by_text("Queued. Run Workspace").wait_for()

    pg.get_by_role("button", name="Streamers").first.click()
    pg.get_by_role("button", name="Show DemoStreamer's clips").wait_for()
    shot(pg, "05-streamers")

    pg.get_by_role("button", name="Queue").first.click()
    pg.get_by_text("Waiting").first.wait_for()

    pg.get_by_role("button", name="Settings").first.click()
    pg.get_by_text("Who watches the clips").wait_for()
    pg.get_by_role("tab", name="Claude (online)").click()
    pg.get_by_text("per 1,000 clips").wait_for()
    shot(pg, "06-settings")
    pg.get_by_role("button", name="Save").first.click()
    pg.get_by_text("Saved", exact=True).wait_for()

    pg.get_by_role("tab", name="Light").click()
    pg.get_by_role("button", name="Clips").first.click()
    pg.get_by_test_id("clip-card").first.wait_for()
    shot(pg, "07-light")


def test_mobile_layout(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch(p)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                  has_touch=True, color_scheme="dark")
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(server)
        pg.get_by_test_id("clip-card").first.wait_for()
        # No horizontal scrolling at phone width
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390
        shot(pg, "08-mobile")
        pg.get_by_test_id("clip-card").first.click()
        pg.get_by_role("dialog").get_by_role("heading", name="What happens").wait_for()
        shot(pg, "09-mobile-sheet")
        browser.close()
        assert not errors, errors


def test_paused_queue_banner_and_resume(page, server):
    import httpx
    pg = page
    httpx.post(server + "/api/jobs/pause", headers={"X-Clip-Manager": "1"}).raise_for_status()
    pg.get_by_role("button", name="Queue").first.click()
    banner = pg.get_by_role("alert")
    expect(banner).to_contain_text("Analysis paused")
    expect(banner).to_contain_text("Waiting clips are kept")
    banner.get_by_role("button", name="Resume").click()
    expect(pg.get_by_role("alert")).to_have_count(0)
    assert httpx.get(server + "/api/jobs").json()["paused"] is None


def test_old_failures_are_marked_as_before_restart(page, server):
    """Failures from before the app started are shown as possibly already fixed."""
    import sqlite3
    import httpx
    con = sqlite3.connect(DATA / "clips.db")
    con.execute("INSERT INTO jobs (kind, clip_id, params, status, message, created_at, updated_at) "
                "VALUES ('analyze', 'old1', '{}', 'error', ?, 0, 1)",
                ["open() got an unexpected keyword argument 'metadata_errors'"])
    con.commit()
    con.close()
    pg = page
    pg.get_by_role("button", name="Queue").first.click()
    section = pg.get_by_role("region", name="Why clips failed")
    expect(section).to_contain_text("metadata_errors")
    expect(section).to_contain_text("Before the last restart")
    httpx.post(server + "/api/jobs/clear-finished", headers={"X-Clip-Manager": "1"})
