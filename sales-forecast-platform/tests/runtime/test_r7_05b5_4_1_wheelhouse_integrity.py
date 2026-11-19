"""Offline F-03 candidate manifest/lock/wheel consistency tests."""
import hashlib
import importlib
import json
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def sample(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    v = importlib.import_module("verify_client_wheelhouse_candidate")
    names = [
        ("pytest", "8.4.2"), ("pydantic", "2.13.3"),
        ("pandas", "2.3.3"), ("numpy", "2.4.4"),
        ("psycopg2-binary", "2.9.12"),
    ] + [(f"mockpkg{i}", "1.0") for i in range(11)]
    entries = []
    for name, version in names:
        filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        path = tmp_path / filename
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(dict(name=name, version=version, filename=filename, sha256=digest))
    driver = next(e for e in entries if e["name"] == "psycopg2-binary")
    monkeypatch.setattr(v, "ORIGINAL_WHEEL", driver["filename"])
    monkeypatch.setattr(v, "ORIGINAL_HASH", driver["sha256"])
    manifest = dict(status="CANDIDATE_ONLY_NOT_APPROVED",
                    python="3.11", platform="linux/amd64", wheels=entries)
    (tmp_path / "candidate-manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "candidate-requirements.lock").write_text("".join(
        f'{e["name"]}=={e["version"]} --hash=sha256:{e["sha256"]}\n' for e in entries))
    return v, tmp_path, entries


def test_matching_manifest_lock_and_wheels(sample):
    v, folder, _ = sample
    assert v.verify_candidate_wheelhouse(folder)


def test_missing_wheel_rejected(sample):
    v, folder, entries = sample
    (folder / entries[0]["filename"]).unlink()
    with pytest.raises(ValueError, match="missing, extra"):
        v.verify_candidate_wheelhouse(folder)


def test_extra_file_rejected(sample):
    v, folder, _ = sample
    (folder / "unknown.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="missing, extra"):
        v.verify_candidate_wheelhouse(folder)


def test_wheel_byte_tampering_rejected(sample):
    v, folder, entries = sample
    with (folder / entries[0]["filename"]).open("ab") as f:
        f.write(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        v.verify_candidate_wheelhouse(folder)


def test_mismatched_lock_hash_rejected(sample):
    v, folder, _ = sample
    path = folder / "candidate-requirements.lock"
    path.write_text(path.read_text().replace("--hash=sha256:", "--hash=sha256:" + "0", 1))
    with pytest.raises(ValueError, match="invalid or duplicate lock"):
        v.verify_candidate_wheelhouse(folder)


def test_manifest_wrong_platform_rejected(sample):
    v, folder, _ = sample
    path = folder / "candidate-manifest.json"
    obj = json.loads(path.read_text())
    obj["platform"] = "linux/arm64"
    path.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match="manifest target"):
        v.verify_candidate_wheelhouse(folder)


def test_manifest_symlink_rejected(sample):
    v, folder, _ = sample
    path = folder / "candidate-manifest.json"
    another = folder / "original.json"
    path.rename(another)
    path.symlink_to(another)
    with pytest.raises(ValueError, match="absent or symlinked"):
        v.verify_candidate_wheelhouse(folder)


def test_duplicate_lock_entry_rejected(sample):
    v, folder, _ = sample
    lock = folder / "candidate-requirements.lock"
    lock.write_text(lock.read_text() + lock.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="duplicate lock"):
        v.verify_candidate_wheelhouse(folder)
