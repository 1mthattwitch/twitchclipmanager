"""Static checks for the Windows .bat files (they can't run in CI, so guard the usual mistakes)."""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BATS = sorted([*ROOT.glob("*.bat"), *ROOT.glob("scripts/*.bat")])

# ClipManager.bat is what a running window has open, so updates must never need to replace it.
# If you really have to change it, update this hash AND expect a one-off manual update for users.
FROZEN_STUB_SHA256 = "c90be1c7d4c5dcfa9f957f1f304dec3120eb953ddc9cce9b0a94e2ebf925cc50"


def test_all_scripts_found():
    names = {p.name for p in BATS}
    assert {"install.bat", "ClipManager.bat", "launch.bat", "update.bat", "setup-deps.bat"} <= names


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_crlf_and_ascii(bat):
    data = bat.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b""), "cmd needs CRLF line endings (labels/goto break with LF)"
    data.decode("ascii")  # non-ASCII text shows up garbled in cmd's default code page


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_goto_and_call_targets_exist(bat):
    text = bat.read_text()
    labels = {m.lower() for m in re.findall(r"^:([A-Za-z_][\w-]*)", text, re.M)}
    for target in re.findall(r"(?:goto|call)\s+:([A-Za-z_][\w-]*)", text, re.I):
        if target.lower() != "eof":
            assert target.lower() in labels, f"missing label :{target}"


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_no_and_or_chaining(bat):
    # Explicit "if errorlevel" checks behave the same on every cmd; && / || don't (Wine, some edge cases).
    for n, line in enumerate(bat.read_text().splitlines(), 1):
        if line.strip().upper().startswith("REM"):
            continue
        assert "&&" not in line and "||" not in line, f"line {n}: {line}"


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_parentheses_balance(bat):
    """An unescaped ')' in an echo inside a ( ... ) block ends the block early."""
    depth = 0
    for n, line in enumerate(bat.read_text().splitlines(), 1):
        s = line.strip()
        if s.upper().startswith("REM") or s.startswith("::"):
            continue
        in_quotes = False
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "^":
                i += 2
                continue
            if ch == '"':
                in_quotes = not in_quotes
            elif not in_quotes and ch == "(":
                depth += 1
            elif not in_quotes and ch == ")":
                depth -= 1
                assert depth >= 0, f"line {n}: unmatched ')': {line}"
            i += 1
    assert depth == 0, "a ( block is never closed"


def test_stub_is_frozen():
    digest = hashlib.sha256((ROOT / "ClipManager.bat").read_bytes()).hexdigest()
    assert digest == FROZEN_STUB_SHA256, (
        "ClipManager.bat changed. It's open while the app runs, so updates shouldn't touch it. "
        "Put new launcher logic in scripts/launch.bat instead.")


def test_gitattributes_keeps_crlf():
    # "-text" stores the CRLF bytes as-is, so raw.githubusercontent.com and ZIP downloads get CRLF too.
    # ("text eol=crlf" would store LF and only convert on checkout, breaking a downloaded install.bat.)
    assert "*.bat -text" in (ROOT / ".gitattributes").read_text()


def test_committed_bytes_are_crlf():
    """What GitHub serves is the stored blob; check it, not just the working tree."""
    import shutil
    import subprocess
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for bat in BATS:
        rel = bat.relative_to(ROOT).as_posix()
        r = subprocess.run(["git", "cat-file", "-p", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            continue  # not committed yet
        assert b"\n" not in r.stdout.replace(b"\r\n", b""), f"{rel} is stored with LF line endings"


def test_launchers_hand_over_to_temp_copies():
    stub = (ROOT / "ClipManager.bat").read_text()
    assert '"%TEMP%\\clipmanager-launch.bat"' in stub and "call " not in stub.split("copy /y", 1)[1]
    launch = (ROOT / "scripts" / "launch.bat").read_text()
    assert 'call "%TEMP%\\clipmanager-update.bat"' in launch, "the updater must run from a temp copy"
    install = (ROOT / "install.bat").read_text()
    assert '"%TEMP%\\clipmanager-install.bat" --from-temp' in install
