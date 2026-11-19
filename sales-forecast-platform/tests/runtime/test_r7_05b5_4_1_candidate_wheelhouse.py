"""Offline safety tests for candidate wheelhouse inventory; no PyPI or Docker."""
import importlib
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def preparer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("prepare_client_wheelhouse_candidate")


def wheel(directory, name, version, payload=b"data"):
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    file = directory / filename
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                         f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr("payload.txt", payload)
    return file


def test_inventory_generates_strict_hash_lock(preparer, tmp_path):
    wheel(tmp_path, "pytest", "8.4.2")
    wheel(tmp_path, "pydantic", "2.13.3")
    inventory = preparer.inspect_wheels(tmp_path)
    lock = preparer.candidate_lock(inventory)
    assert "pytest==8.4.2 --hash=sha256:" in lock
    assert "pydantic==2.13.3 --hash=sha256:" in lock
    assert len(lock.strip().splitlines()) == 2
    assert preparer.validate_inventory is not None


def test_candidate_lock_still_rejects_missing_transitives(preparer, tmp_path):
    wheel(tmp_path, "pytest", "8.4.2")
    inventory = preparer.inspect_wheels(tmp_path)
    with pytest.raises(ValueError, match="top-level requirement unresolved"):
        preparer.validate_inventory(inventory)


def test_duplicate_distribution_fails_closed(preparer, tmp_path):
    wheel(tmp_path, "pytest", "8.4.2")
    file = wheel(tmp_path, "pytest", "8.4.3")
    assert file.exists()
    with pytest.raises(ValueError, match="duplicate distribution"):
        preparer.inspect_wheels(tmp_path)


def test_non_wheel_or_symlink_blocks_inventory(preparer, tmp_path):
    wheel(tmp_path, "pytest", "8.4.2")
    (tmp_path / "unexpected.txt").write_text("not a wheel")
    with pytest.raises(ValueError, match="only regular"):
        preparer.inspect_wheels(tmp_path)


def test_original_psycopg_hash_must_match(preparer):
    items = [{"name": x.split("==")[0].replace("_", "-"), "version": x.split("==")[1],
              "filename": "candidate.whl", "sha256": "0"*64}
             for x in preparer.TOP_LEVEL]
    with pytest.raises(ValueError, match="approved wheel differs"):
        preparer.validate_inventory(items)
