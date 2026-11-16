"""No-network tests: v2 uses new path; verified v1 is never mutated."""
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    mod = importlib.import_module("prepare_client_wheelhouse_v2_candidate")
    old = tmp_path / "v1"
    old.mkdir()
    items = []
    names = ["pytest", "pydantic", "pandas", "numpy", "psycopg2-binary"] + [
        f"fakedep{i}" for i in range(11)]
    for name in names:
        version = "8.4.2" if name == "pytest" else "1.0"
        filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        (old / filename).write_bytes(filename.encode())
        items.append({"name": name, "version": version, "filename": filename,
                      "sha256": "f"*64})
    (old / "candidate-manifest.json").write_text(json.dumps({"wheels": items}))
    (old / "candidate-requirements.lock").write_text("old lock")
    original = {f.name: f.read_bytes() for f in old.iterdir()}
    monkeypatch.setattr(mod, "verify_candidate_wheelhouse", lambda p: True)
    def fake_inspect(folder):
        out = []
        for p in folder.glob("*.whl"):
            stem = p.name.split("-")
            name = stem[0].replace("_", "-")
            ver = stem[1]
            out.append({"name": name, "version": ver, "filename": p.name,
                        "sha256": "f"*64})
        return out
    monkeypatch.setattr(mod, "inspect_wheels", fake_inspect)
    driver = next(i for i in items if i["name"] == "psycopg2-binary")
    monkeypatch.setattr(mod, "ORIGINAL_WHEEL", driver["filename"])
    monkeypatch.setattr(mod, "ORIGINAL_HASH", driver["sha256"])
    # Pin the test-only fake versions; the source code's actual pinned top-level
    # package versions are independently covered by the normal verification.
    monkeypatch.setattr(mod, "V2_TOP_LEVEL", ("pytest==9.0.3",))
    def downloader(cmd, *, check, timeout):
        assert check and timeout == 300
        assert cmd[-1] == "pytest==9.0.3"
        assert "--no-deps" in cmd
        (Path(cmd[cmd.index("--dest")+1]) / "pytest-9.0.3-py3-none-any.whl").write_bytes(b"new")
    return mod, old, tmp_path / "v2", original, downloader


def test_new_wheelhouse_replaces_only_pytest(case):
    mod, source, dest, original, downloader = case
    result = mod.prepare_v2(source, dest, downloader=downloader)
    assert result["wheel_count"] == 16
    assert result["pytest_version"] == "9.0.3"
    assert not (dest / "pytest-8.4.2-py3-none-any.whl").exists()
    assert (dest / "pytest-9.0.3-py3-none-any.whl").exists()
    assert not (dest / "INCOMPLETE_DO_NOT_USE").exists()
    assert {f.name: f.read_bytes() for f in source.iterdir()} == original


def test_existing_destination_blocks_before_downloading(case):
    mod, source, dest, original, downloader = case
    dest.mkdir()
    with pytest.raises(ValueError, match="destination must be"):
        mod.prepare_v2(source, dest, downloader=downloader)
    assert {f.name: f.read_bytes() for f in source.iterdir()} == original


def test_source_verification_blocks_before_destination(case, monkeypatch):
    mod, source, dest, _, downloader = case
    monkeypatch.setattr(mod, "verify_candidate_wheelhouse",
                        lambda _: (_ for _ in ()).throw(ValueError("bad source")))
    with pytest.raises(ValueError, match="bad source"):
        mod.prepare_v2(source, dest, downloader=downloader)
    assert not dest.exists()


def test_failed_download_preserves_incomplete_sentinel(case):
    mod, source, dest, _, _ = case
    def failure(*args, **kwargs):
        raise RuntimeError("no network")
    with pytest.raises(RuntimeError, match="no network"):
        mod.prepare_v2(source, dest, downloader=failure)
    assert (dest / "INCOMPLETE_DO_NOT_USE").exists()


def test_missing_new_pytest_is_rejected(case):
    mod, source, dest, _, _ = case
    def no_download(*args, **kwargs):
        pass
    with pytest.raises(ValueError, match="exactly 16"):
        mod.prepare_v2(source, dest, downloader=no_download)
    assert (dest / "INCOMPLETE_DO_NOT_USE").exists()
