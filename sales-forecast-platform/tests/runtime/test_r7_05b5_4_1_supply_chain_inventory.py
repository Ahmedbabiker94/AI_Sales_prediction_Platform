"""Offline tests for F-03 untrusted metadata inventory (no PyPI or Docker)."""
import importlib
import json
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("review_client_supply_chain_candidate")
    monkeypatch.setattr(module, "verify_candidate_wheelhouse", lambda _: True)
    filename = "demo-1.0-py3-none-any.whl"
    manifest = {"wheels": [{"name": "demo", "version": "1.0",
                           "filename": filename, "sha256": "a" * 64}]}
    (tmp_path / "candidate-manifest.json").write_text(json.dumps(manifest))
    def make_wheel(extra="", license_file=False):
        with zipfile.ZipFile(tmp_path / filename, "w") as z:
            z.writestr("demo-1.0.dist-info/METADATA",
                       "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n" + extra)
            if license_file:
                z.writestr("demo-1.0.dist-info/licenses/LICENSE.txt", "Example")
    make_wheel()
    return module, tmp_path, make_wheel


def test_missing_license_is_flagged_not_approved(source):
    module, folder, _ = source
    result = module.inventory_candidate_supply_chain(folder)
    assert result["license_information_missing"] == 1
    assert result["status"] == "REVIEW_REQUIRED_NOT_APPROVED"
    assert not result["packages"][0]["pypi_artifact_provenance_verified"]


def test_license_expression_is_reported_but_not_approved(source):
    module, folder, make_wheel = source
    make_wheel("License-Expression: MIT\n", license_file=True)
    result = module.inventory_candidate_supply_chain(folder)
    assert result["license_information_missing"] == 0
    assert result["packages"][0]["license_expression"] == "MIT"
    assert result["packages"][0]["embedded_license_files"]
    assert result["packages"][0]["needs_license_manual_review"]


def test_legacy_license_classifier_is_reported(source):
    module, folder, make_wheel = source
    make_wheel("Classifier: License :: OSI Approved :: MIT License\n")
    assert module.inventory_candidate_supply_chain(folder)["license_information_missing"] == 0


def test_invalid_metadata_fails_closed(source):
    module, folder, _ = source
    path = folder / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("payload.txt", "no metadata")
    with pytest.raises(ValueError, match="ambiguous package metadata"):
        module.inventory_candidate_supply_chain(folder)


def test_original_integrity_gate_is_always_called(source, monkeypatch):
    module, folder, _ = source
    monkeypatch.setattr(module, "verify_candidate_wheelhouse",
                        lambda _: (_ for _ in ()).throw(ValueError("hash mismatch")))
    with pytest.raises(ValueError, match="hash mismatch"):
        module.inventory_candidate_supply_chain(folder)
