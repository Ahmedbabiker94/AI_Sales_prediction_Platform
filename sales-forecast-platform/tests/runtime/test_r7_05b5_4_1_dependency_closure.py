"""Offline wheel METADATA dependency closure tests, no Docker or PyPI."""
import importlib
import json
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("verify_client_dependency_closure_candidate")
    monkeypatch.setattr(module, "verify_candidate_wheelhouse", lambda _: True)
    def write(name, requires=(), requires_python=">=3.11"):
        filename = f"{name}-1.0-py3-none-any.whl"
        header = (f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
                  + f"Requires-Python: {requires_python}\n"
                  + "".join(f"Requires-Dist: {r}\n" for r in requires))
        with zipfile.ZipFile(tmp_path / filename, "w") as z:
            z.writestr(f"{name}-1.0.dist-info/METADATA", header)
        return dict(name=name, version="1.0", filename=filename, sha256="0"*64)
    return module, tmp_path, write


def save_manifest(folder, entries):
    (folder / "candidate-manifest.json").write_text(json.dumps({"wheels": entries}))


def test_complete_base_dependency_succeeds(sample):
    v, folder, write = sample
    save_manifest(folder, [write("demo", ["helper>=1.0"]), write("helper")])
    assert v.verify_candidate_dependency_closure(folder)["active_base_dependencies_satisfied"] == 1


def test_missing_dependency_blocks(sample):
    v, folder, write = sample
    save_manifest(folder, [write("demo", ["missing>=1.0"])])
    with pytest.raises(ValueError, match="missing or incompatible"):
        v.verify_candidate_dependency_closure(folder)


def test_version_constraint_blocks(sample):
    v, folder, write = sample
    save_manifest(folder, [write("demo", ["helper>=2.0"]), write("helper")])
    with pytest.raises(ValueError, match="missing or incompatible"):
        v.verify_candidate_dependency_closure(folder)


def test_inactive_optional_extras_do_not_block_base(sample):
    v, folder, write = sample
    save_manifest(folder, [write("demo", ['missing>=1.0; extra == "optional"'])])
    assert v.verify_candidate_dependency_closure(folder)["active_base_dependencies_satisfied"] == 0


def test_incompatible_python_rejected(sample):
    v, folder, write = sample
    save_manifest(folder, [write("demo", requires_python=">=3.12")])
    with pytest.raises(ValueError, match="Requires-Python"):
        v.verify_candidate_dependency_closure(folder)
