"""CR-0: candidate-v2 license inventory must use v2 hash/version gate.

No network, Docker, PostgreSQL or package downloads. This test only proves
input-gate routing and non-approval flags, not wheel authenticity/licenses.
"""
import importlib
import json
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def candidate(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("review_client_supply_chain_candidate")
    v2 = importlib.import_module("prepare_client_wheelhouse_v2_candidate")
    filename = "demo-1.0-py3-none-any.whl"
    (tmp_path / "candidate-manifest.json").write_text(json.dumps({
        "wheels": [{"name": "demo", "version": "1.0", "filename": filename,
                    "sha256": "a" * 64}]
    }), encoding="utf-8")
    with zipfile.ZipFile(tmp_path / filename, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA",
                         "Metadata-Version: 2.4\nName: demo\nVersion: 1.0\nLicense-Expression: MIT\n")
    return module, v2, tmp_path


def test_v2_license_inventory_requires_correct_version_specific_integrity_gate(
        candidate, monkeypatch):
    module, v2, folder = candidate
    seen = []

    def checked(directory, **kwargs):
        seen.append((directory, kwargs))
        assert kwargs == {"top_level": v2.V2_TOP_LEVEL}
        return True

    monkeypatch.setattr(module, "verify_candidate_wheelhouse", checked)
    result = module.inventory_candidate_supply_chain(folder, candidate_version="v2")
    assert seen == [(folder, {"top_level": v2.V2_TOP_LEVEL})]
    assert result["status"] == "REVIEW_REQUIRED_NOT_APPROVED"
    assert result["packages"][0]["needs_license_manual_review"] is True
    assert result["packages"][0]["pypi_artifact_provenance_verified"] is False
    assert result["packages"][0]["vulnerability_review_completed"] is False


def test_v1_default_preserves_existing_integrity_interface(candidate, monkeypatch):
    module, _, folder = candidate
    calls = []

    def checked(directory, **kwargs):
        calls.append(kwargs)
        assert kwargs == {}
        return True

    monkeypatch.setattr(module, "verify_candidate_wheelhouse", checked)
    result = module.inventory_candidate_supply_chain(folder)
    assert result["wheels_verified"] == 1
    assert calls == [{}]


@pytest.mark.parametrize("bad", ["v3", "", None, 2])
def test_unknown_candidate_version_blocks_before_reading_package_metadata(
        candidate, monkeypatch, bad):
    module, _, folder = candidate
    def forbidden(*args, **kwargs):
        pytest.fail("invalid version must not inspect artifact")
    monkeypatch.setattr(module, "verify_candidate_wheelhouse", forbidden)
    with pytest.raises(ValueError, match="unsupported candidate version"):
        module.inventory_candidate_supply_chain(folder, candidate_version=bad)


def test_v2_integrity_failure_blocks_license_inventory(candidate, monkeypatch):
    module, _, folder = candidate
    def invalid(*args, **kwargs):
        raise ValueError("wheel SHA-256 mismatch")
    monkeypatch.setattr(module, "verify_candidate_wheelhouse", invalid)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.inventory_candidate_supply_chain(folder, candidate_version="v2")


def test_v1_and_v2_do_not_silently_substitute_in_review(candidate, monkeypatch):
    module, v2, folder = candidate
    def checked(_, **kwargs):
        if kwargs != {"top_level": v2.V2_TOP_LEVEL}:
            raise ValueError("wrong candidate top-level")
        return True
    monkeypatch.setattr(module, "verify_candidate_wheelhouse", checked)
    with pytest.raises(ValueError, match="wrong candidate top-level"):
        module.inventory_candidate_supply_chain(folder)
    assert module.inventory_candidate_supply_chain(
        folder, candidate_version="v2")["status"] == "REVIEW_REQUIRED_NOT_APPROVED"
