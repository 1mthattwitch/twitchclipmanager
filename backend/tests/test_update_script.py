"""The auto-update logic, exercised against real git repos (scripts/update.sh mirrors update.bat)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "scripts" / "update.sh"

pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git + bash")


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repos(tmp_path):
    """A fake GitHub (bare repo), a maintainer checkout that pushes to it, and the user's install."""
    remote, dev, app = tmp_path / "remote.git", tmp_path / "dev", tmp_path / "app"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(dev))
    (dev / "app.txt").write_text("v1\n")
    (dev / ".gitignore").write_text("data/\n")
    git(dev, "add", "-A"); git(dev, "commit", "-qm", "v1"); git(dev, "push", "-q", "origin", "HEAD:main")
    git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(app))

    def publish(text):
        (dev / "app.txt").write_text(text)
        git(dev, "commit", "-qam", text.strip()); git(dev, "push", "-q", "origin", "HEAD:main")
    return remote, app, publish


def run(app):
    r = subprocess.run(["bash", str(UPDATE), str(app)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "the updater must never block startup"
    return r.stdout


def test_up_to_date(repos):
    _, app, _ = repos
    assert "latest version" in run(app)


def test_pulls_new_version(repos):
    _, app, publish = repos
    publish("v2\n"); publish("v3\n")
    out = run(app)
    assert "Updated to the latest version. 2 change(s)." in out
    assert (app / "app.txt").read_text() == "v3\n"
    assert "latest version" in run(app)


def test_offline_keeps_current_version(repos):
    _, app, publish = repos
    publish("v2\n")
    git(app, "remote", "set-url", "origin", str(app.parent / "gone.git"))
    assert "Couldn't reach GitHub" in run(app)
    assert (app / "app.txt").read_text() == "v1\n"


def test_local_edits_block_update_and_keep_user_data(repos):
    _, app, publish = repos
    (app / "data").mkdir()
    (app / "data" / "settings.json").write_text('{"keep": true}')
    (app / "app.txt").write_text("my edit\n")
    publish("v2\n")
    assert "files in the app folder were edited" in run(app)
    assert (app / "app.txt").read_text() == "my edit\n"
    assert (app / "data" / "settings.json").read_text() == '{"keep": true}'


def test_user_data_survives_updates(repos):
    _, app, publish = repos
    (app / "data").mkdir()
    (app / "data" / "clips.db").write_bytes(b"precious")
    publish("v2\n")
    assert "Updated" in run(app)
    assert (app / "data" / "clips.db").read_bytes() == b"precious"


def test_diverged_copy_is_left_alone(repos):
    _, app, publish = repos
    (app / "app.txt").write_text("local commit\n")
    git(app, "commit", "-qam", "local")
    publish("v2\n")
    assert "changes that aren't on GitHub" in run(app)
    assert (app / "app.txt").read_text() == "local commit\n"


def test_no_git_folder(tmp_path):
    assert "Auto-update is off" in run(tmp_path)


def test_switches_channel_after_merge(tmp_path):
    remote, dev, app = tmp_path / "r.git", tmp_path / "dev", tmp_path / "app"
    git(tmp_path, "init", "-q", "--bare", "-b", "feature", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(dev))
    (dev / "ClipManager.bat").write_text("stub\r\n")
    git(dev, "add", "-A"); git(dev, "commit", "-qm", "app"); git(dev, "push", "-q", "origin", "HEAD:feature")
    git(tmp_path, "clone", "-q", "-b", "feature", str(remote), str(app))
    # main created at an older commit without the app: no switch
    git(dev, "checkout", "-q", "--orphan", "old"); git(dev, "rm", "-rqf", ".")
    (dev / "README").write_text("old\n"); git(dev, "add", "-A"); git(dev, "commit", "-qm", "old")
    git(dev, "push", "-q", "origin", "HEAD:main")
    run(app)
    assert git(app, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    # PR merged: main now contains the app and this version -> switch
    git(dev, "checkout", "-q", "feature"); git(dev, "push", "-q", "-f", "origin", "HEAD:main")
    out = run(app)
    assert "Switched to the main update channel" in out
    assert git(app, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(app, "rev-parse", "--abbrev-ref", "main@{u}") == "origin/main"
