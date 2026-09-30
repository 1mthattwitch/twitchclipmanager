"""Existing Ollama model folders, choosing between them, and driving Ollama."""
import json
from pathlib import Path

import httpx
import pytest

from app import config
from app.services import ollama_manager, ollama_stores


def make_store(root: Path, models: dict[str, dict] | None = None, nested: str = "") -> Path:
    """Build a fake Ollama store. models: name -> {"vision": bool, "complete": bool, "size": int}."""
    store = root / nested if nested else root
    (store / "blobs").mkdir(parents=True, exist_ok=True)
    (store / "manifests").mkdir(parents=True, exist_ok=True)
    for i, (name, opts) in enumerate((models or {}).items()):
        base, tag = name.split(":") if ":" in name else (name, "latest")
        digest = f"sha256:{i:064d}"
        layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": opts.get("size", 100)}]
        if opts.get("projector"):
            layers.append({"mediaType": ollama_stores.PROJECTOR_MEDIA, "digest": f"sha256:{i + 500:064d}", "size": 5})
        mf = store / "manifests" / "registry.ollama.ai" / "library" / base / tag
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"schemaVersion": 2, "config": {"digest": f"sha256:{i + 900:064d}", "size": 1},
                                  "layers": layers}))
        if opts.get("complete", True):
            for layer in layers + [{"digest": f"sha256:{i + 900:064d}"}]:
                (store / "blobs" / layer["digest"].replace(":", "-")).write_bytes(b"x")
    return store


def test_finds_stores_in_nested_layouts(tmp_path):
    flat = make_store(tmp_path / "J" / "ai" / "ollama_models", {"qwen2.5vl:7b": {}})
    nested = make_store(tmp_path / "L" / ".ollama", {"llama3:8b": {}}, nested="models")
    assert ollama_stores.find_stores_under(tmp_path / "J" / "ai" / "ollama_models") == [flat]
    assert ollama_stores.find_stores_under(tmp_path / "L" / ".ollama") == [nested]
    assert ollama_stores.find_stores_under(tmp_path / "missing") == []


def test_inventory_names_vision_and_completeness(tmp_path):
    store = make_store(tmp_path / "s", {
        "qwen2.5vl:7b": {"size": 6_000},
        "llama3:8b": {},
        "somevision:1b": {"projector": True},
        "llava:13b": {"complete": False},
    })
    models = {m.name: m for m in ollama_stores.inventory(store)}
    assert set(models) == {"qwen2.5vl:7b", "llama3:8b", "somevision:1b", "llava:13b"}
    assert models["qwen2.5vl:7b"].vision and models["qwen2.5vl:7b"].complete
    assert not models["llama3:8b"].vision
    assert models["somevision:1b"].vision  # detected from its projector layer
    assert not models["llava:13b"].complete and models["llava:13b"].missing_blobs > 0


def test_other_namespaces_and_registries(tmp_path):
    store = make_store(tmp_path / "s")
    for rel in ("registry.ollama.ai/someuser/cool/q4", "hf.co/org/repo/latest"):
        mf = store / "manifests" / Path(rel)
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"layers": []}))
    names = {m.name for m in ollama_stores.inventory(store)}
    assert names == {"someuser/cool:q4", "hf.co/org/repo:latest"}
    assert ollama_stores.normalize("qwen2.5vl") == "qwen2.5vl:latest"
    assert ollama_stores.normalize("registry.ollama.ai/library/QWEN2.5VL:7b") == "qwen2.5vl:7b"


@pytest.fixture()
def two_drives(tmp_path, monkeypatch):
    L = tmp_path / "L" / ".DoNotTouch" / "models" / ".ollama"
    J = tmp_path / "J" / "ai" / "ollama_models"
    config.save_settings({"ollama_model_dirs": [str(L), str(J)], "ollama_models_dir": ""})
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return L, J


def pick(model="qwen2.5vl:7b"):
    stores, notes = ollama_stores.discover()
    return ollama_stores.choose(stores, model), stores, notes


def test_prefers_L_when_both_have_the_model(two_drives):
    L, J = two_drives
    make_store(L, {"qwen2.5vl:7b": {}}, nested="models")
    make_store(J, {"qwen2.5vl:7b": {}})
    c, stores, _ = pick()
    assert c.store == str(L / "models") and not c.needs_download
    assert [s.path for s in stores] == [str(L / "models"), str(J)]


def test_uses_J_when_only_J_has_the_model(two_drives):
    L, J = two_drives
    make_store(L, {"llama3:8b": {}})
    make_store(J, {"qwen2.5vl:7b": {}})
    c, _, _ = pick()
    assert c.store == str(J) and c.model == "qwen2.5vl:7b" and not c.needs_download


def test_switches_to_an_existing_vision_model_instead_of_downloading(two_drives):
    L, J = two_drives
    make_store(L, {"llama3:8b": {}})
    make_store(J, {"llava:13b": {"size": 900}, "minicpm-v:8b": {"size": 500}})
    c, _, _ = pick()
    assert c.store == str(J) and c.model == "llava:13b" and not c.needs_download


def test_incomplete_model_is_not_used(two_drives):
    L, J = two_drives
    make_store(L, {"qwen2.5vl:7b": {"complete": False}})
    c, _, _ = pick()
    assert c.store == str(L) and c.needs_download  # re-download into L (writing to L is allowed)


def test_downloads_into_L_when_no_vision_model_anywhere(two_drives):
    L, J = two_drives
    make_store(L, {"llama3:8b": {}})
    make_store(J, {"mistral:7b": {}})
    c, _, _ = pick()
    assert c.store == str(L) and c.needs_download


def test_disconnected_drive_falls_back_with_a_note(two_drives):
    L, J = two_drives
    make_store(J, {"qwen2.5vl:7b": {}})
    c, _, notes = pick()
    assert c.store == str(J)
    assert any("folder not found" in n or "isn't connected" in n for n in notes)


def test_nothing_found_uses_default(two_drives):
    c, stores, _ = pick()
    assert stores == [] and c.needs_download and c.store is None


def test_never_modifies_stores(two_drives):
    L, J = two_drives
    store = make_store(L, {"qwen2.5vl:7b": {}, "llava:7b": {"complete": False}})
    before = sorted((p, p.stat().st_mtime_ns) for p in store.rglob("*"))
    ollama_stores.discover(); ollama_manager.plan(http=_fake_ollama(set()))
    after = sorted((p, p.stat().st_mtime_ns) for p in store.rglob("*"))
    assert before == after


# ---------------- manager ----------------

def _fake_ollama(models: set[str] | None, pull_lines: list[dict] | None = None):
    def handler(request: httpx.Request):
        if models is None:
            raise httpx.ConnectError("refused")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in sorted(models)]})
        if request.url.path == "/api/pull":
            body = "\n".join(json.dumps(l) for l in pull_lines or [])
            return httpx.Response(200, content=body.encode())
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mismatch_detected_when_running_ollama_serves_another_folder(two_drives):
    L, _ = two_drives
    make_store(L, {"qwen2.5vl:7b": {}})
    assert ollama_manager.plan(http=_fake_ollama({"llama3:8b"}))["mismatch"] is True
    assert ollama_manager.plan(http=_fake_ollama({"qwen2.5vl:7b"}))["mismatch"] is False
    p = ollama_manager.plan(http=_fake_ollama(None))
    assert p["running"] is False and p["mismatch"] is False


def test_pinned_folder_wins(two_drives):
    L, J = two_drives
    make_store(L, {"qwen2.5vl:7b": {}})
    make_store(J, {"qwen2.5vl:7b": {}})
    config.save_settings({"ollama_models_dir": str(J)})
    assert ollama_manager.plan(http=_fake_ollama(None))["choice"]["store"] == str(J)


class FakeWinreg:
    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved, access):
        assert (root, path) == ("HKCU", "Environment")
        return "key"

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def CloseKey(self, key):
        pass


def test_apply_choice_sets_system_wide_variable(two_drives, monkeypatch):
    L, _ = two_drives
    reg = FakeWinreg()
    done = ollama_manager.apply_choice(str(L), "qwen2.5vl:7b", winreg_mod=reg)
    assert reg.values == {"OLLAMA_MODELS": str(L)}
    assert config.get_settings().ollama_models_dir == str(L)
    assert any("OLLAMA_MODELS" in d for d in done)


class FakeProcesses:
    def __init__(self):
        self.started, self.stopped = [], 0

    def start(self, args, env):
        self.started.append((args, env.get("OLLAMA_MODELS")))

    def stop_ollama(self):
        self.stopped += 1


def test_start_passes_model_folder(two_drives, monkeypatch):
    L, _ = two_drives
    procs = FakeProcesses()
    monkeypatch.setattr(ollama_manager, "PROCESS", procs)
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: "/bin/ollama")
    monkeypatch.setattr(ollama_manager, "find_tray_app", lambda: None)
    calls = {"n": 0}

    def running(http=None):
        calls["n"] += 1
        return None if calls["n"] < 3 else {"qwen2.5vl:7b"}
    monkeypatch.setattr(ollama_manager, "running_models", running)
    monkeypatch.setattr(ollama_manager.time, "sleep", lambda s: None)
    assert ollama_manager.start(str(L)) is True
    assert procs.started == [(["/bin/ollama", "serve"], str(L))]


def test_start_without_ollama_installed(monkeypatch):
    monkeypatch.setattr(ollama_manager, "running_models", lambda http=None: None)
    monkeypatch.setattr(ollama_manager, "find_exe", lambda: None)
    monkeypatch.setattr(ollama_manager, "find_tray_app", lambda: None)
    assert ollama_manager.start() is False


def test_pull_progress_and_errors():
    ok = ollama_manager.pull("qwen2.5vl:7b", http=_fake_ollama(set(), [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "a", "total": 100, "completed": 50},
        {"status": "pulling def", "digest": "b", "total": 100, "completed": 100},
        {"status": "success"},
    ]))
    assert ok["done"] and ok["error"] is None and ok["percent"] == 100.0
    bad = ollama_manager.pull("nope", http=_fake_ollama(set(), [{"error": "pull model manifest: file does not exist"}]))
    assert bad["status"] == "failed" and "does not exist" in bad["error"]
    cut = ollama_manager.pull("x", http=_fake_ollama(set(), [{"status": "pulling", "digest": "a", "total": 10, "completed": 3}]))
    assert cut["status"] == "failed" and "stopped" in cut["error"]


def test_image_generation_models_next_to_the_store_are_ignored(tmp_path, monkeypatch):
    """L:\\.DoNotTouch\\models also holds image-generation models; only the Ollama store is used."""
    models = tmp_path / "L" / ".DoNotTouch" / "models"
    store = make_store(models / ".ollama", {"qwen2.5vl:7b": {}}, nested="models")
    sd = models / "Stable-diffusion"
    sd.mkdir(parents=True)
    (sd / "sdxl_base_1.0.safetensors").write_bytes(b"\0" * 1024)
    (models / "Lora" / "styles").mkdir(parents=True)
    (models / "Lora" / "styles" / "style.safetensors").write_bytes(b"\0" * 64)
    # A Hugging Face cache also has "blobs" but no "manifests": not an Ollama store.
    hf = models / "huggingface" / "hub" / "models--stabilityai--sdxl"
    (hf / "blobs").mkdir(parents=True)
    (hf / "snapshots").mkdir()
    (hf / "blobs" / "abc123").write_bytes(b"\0" * 64)
    before = sorted((p, p.stat().st_mtime_ns, p.stat().st_size) for p in models.rglob("*") if "ollama" not in str(p))

    opened = []
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(self), real_read(self, *a, **k))[1])
    # Whether the configured folder is the .ollama folder or its parent "models" folder:
    for root in (models / ".ollama", models):
        found = ollama_stores.find_stores_under(root)
        assert found == [store], root
        stores, _ = ollama_stores.discover([str(root)])
        assert [s.path for s in stores] == [str(store)]
    assert all(str(store) in str(p) for p in opened), "only Ollama manifests may be read"
    after = sorted((p, p.stat().st_mtime_ns, p.stat().st_size) for p in models.rglob("*") if "ollama" not in str(p))
    assert before == after
