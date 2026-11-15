"""Offline tests for the read-only OCI identity verifier. NO Docker calls."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def check(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    verifier = importlib.import_module("verify_postgres_oci")
    index = {"mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [
                 {"digest": verifier.MANIFEST,
                  "mediaType": "application/vnd.oci.image.manifest.v1+json",
                  "platform": {"os": "linux", "architecture": "amd64"}},
                 {"digest": "sha256:" + "1"*64,
                  "mediaType": "application/vnd.oci.image.manifest.v1+json",
                  "platform": {"os": "linux", "architecture": "arm64"}},
             ]}
    manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": verifier.CONFIG,
                           "mediaType": "application/vnd.oci.image.config.v1+json"}}
    def setup(*, local=None, index_override=None, manifest_override=None, failing=None):
        calls = []
        def fake(argv, **kwargs):
            calls.append(argv)
            assert kwargs["timeout"] <= 25
            assert kwargs["check"] is False
            assert argv[0] == "docker"
            assert argv[1:3] != ["pull", "run"]
            assert "build" not in argv[:2]
            if failing is not None and len(calls) == failing:
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            if len(calls) == 1:
                assert argv[:4] == ["docker", "--host=unix:///var/run/docker.sock",
                                    "image", "inspect"]
                assert "--platform" in argv
                return SimpleNamespace(returncode=0, stdout=(local or verifier.INDEX)
                                       + " linux amd64\n", stderr="")
            if len(calls) == 2:
                assert argv[2:4] == ["imagetools", "inspect"]
                assert verifier.INDEX in argv[4]
                return SimpleNamespace(returncode=0,
                                       stdout=json.dumps(index_override if index_override is not None else index),
                                       stderr="")
            assert len(calls) == 3
            assert verifier.MANIFEST in argv[4]
            return SimpleNamespace(returncode=0,
                                   stdout=json.dumps(manifest_override if manifest_override is not None else manifest),
                                   stderr="")
        monkeypatch.setattr(verifier.subprocess, "run", fake)
        return calls
    return SimpleNamespace(verifier=verifier, index=index, manifest=manifest, setup=setup)


@pytest.mark.parametrize("local_id", ["index", "manifest", "config"])
def test_accept_only_matching_local_and_remote_identity(check, local_id):
    v = check.verifier
    ids = {"index": v.INDEX, "manifest": v.MANIFEST, "config": v.CONFIG}
    calls = check.setup(local=ids[local_id])
    assert v.verified_postgres_oci() is True
    assert len(calls) == 3


def test_reject_wrong_local_identity_before_registry_queries(check):
    calls = check.setup(local="sha256:" + "0"*64)
    assert check.verifier.verified_postgres_oci() is False
    assert len(calls) == 1


def test_reject_changed_manifest_reference(check):
    index = json.loads(json.dumps(check.index))
    index["manifests"][0]["digest"] = "sha256:" + "0"*64
    check.setup(index_override=index)
    assert check.verifier.verified_postgres_oci() is False


def test_reject_wrong_config_digest(check):
    manifest = json.loads(json.dumps(check.manifest))
    manifest["config"]["digest"] = "sha256:" + "0"*64
    check.setup(manifest_override=manifest)
    assert check.verifier.verified_postgres_oci() is False


def test_reject_ambiguous_amd64_entries(check):
    index = json.loads(json.dumps(check.index))
    index["manifests"].append(dict(index["manifests"][0]))
    check.setup(index_override=index)
    assert check.verifier.verified_postgres_oci() is False


@pytest.mark.parametrize("failed_call", [1, 2, 3])
def test_any_docker_metadata_failure_blocks(check, failed_call):
    check.setup(failing=failed_call)
    assert check.verifier.verified_postgres_oci() is False


def test_no_fallback_for_wrong_manifest_media_type(check):
    manifest = json.loads(json.dumps(check.manifest))
    manifest["mediaType"] = "unsupported"
    check.setup(manifest_override=manifest)
    assert check.verifier.verified_postgres_oci() is False
