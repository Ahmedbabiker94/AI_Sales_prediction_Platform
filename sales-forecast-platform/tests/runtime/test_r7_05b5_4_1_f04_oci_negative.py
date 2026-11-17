"""F-04 additional OCI verifier negative scenarios; metadata is mocked.

No Docker, network, PostgreSQL, image pulls/builds or runner changes.
This tests a read-only candidate verifier, NOT approved runner wiring.
"""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("verify_postgres_oci")


@pytest.mark.parametrize("bad_index", [
    [],
    None,
    {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": None},
    {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": "unexpected"},
    {"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [{}]},
    {"mediaType": "application/vnd.oci.image.index.v1+json",
     "manifests": [{"platform": {"os": "linux", "architecture": "amd64"},
                    "digest": "sha256:" + "0" * 64,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json"}]},
])
def test_malformed_or_untrusted_index_metadata_fails_closed(verifier, monkeypatch, bad_index):
    calls = []
    def fake_output(argv):
        calls.append(argv)
        if len(calls) == 1:
            return verifier.INDEX + " linux amd64\n"
        if len(calls) == 2:
            return json.dumps(bad_index)
        return json.dumps({
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": verifier.CONFIG,
                       "mediaType": "application/vnd.oci.image.config.v1+json"}})
    monkeypatch.setattr(verifier, "_output", fake_output)
    assert verifier.verified_postgres_oci() is False
    assert all("pull" not in argv and "run" not in argv and "build" not in argv
               for argv in calls)


@pytest.mark.parametrize("bad_manifest", [
    [],
    None,
    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": None},
    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": []},
    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": {}},
])
def test_malformed_manifest_metadata_fails_closed(verifier, monkeypatch, bad_manifest):
    index = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "digest": verifier.MANIFEST,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "platform": {"os": "linux", "architecture": "amd64"}}],
    }
    calls = []
    def fake_output(argv):
        calls.append(argv)
        if len(calls) == 1:
            return verifier.INDEX + " linux amd64\n"
        return json.dumps(index if len(calls) == 2 else bad_manifest)
    monkeypatch.setattr(verifier, "_output", fake_output)
    assert verifier.verified_postgres_oci() is False
    assert len(calls) == 3


@pytest.mark.parametrize("local", [
    "",
    "sha256:" + "0" * 64 + " linux amd64\n",
    "sha256:" + "a" * 64 + " linux arm64\n",
    "sha256:" + "a" * 64 + " windows amd64\n",
    "sha256:" + "a" * 64 + " linux amd64 extra\n",
])
def test_untrusted_local_identity_never_queries_remote_metadata(
        verifier, monkeypatch, local):
    calls = []
    def fake_output(argv):
        calls.append(argv)
        assert len(calls) == 1, "remote lookup must not follow bad local identity"
        return local
    monkeypatch.setattr(verifier, "_output", fake_output)
    assert verifier.verified_postgres_oci() is False
    assert len(calls) == 1


@pytest.mark.parametrize("metadata_call", [1, 2, 3])
def test_missing_oci_metadata_fails_closed_without_fallback(
        verifier, monkeypatch, metadata_call):
    calls = []
    def fake_output(argv):
        calls.append(argv)
        if len(calls) == metadata_call:
            return None
        if len(calls) == 1:
            return verifier.INDEX + " linux amd64\n"
        if len(calls) == 2:
            return json.dumps({
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [{
                    "digest": verifier.MANIFEST,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"os": "linux", "architecture": "amd64"}}]})
        return json.dumps({
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": verifier.CONFIG,
                       "mediaType": "application/vnd.oci.image.config.v1+json"}})
    monkeypatch.setattr(verifier, "_output", fake_output)
    assert verifier.verified_postgres_oci() is False


def test_candidate_uses_only_explicit_immutable_metadata_lookups(verifier, monkeypatch):
    observed = []
    index = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "digest": verifier.MANIFEST,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "platform": {"os": "linux", "architecture": "amd64"}}],
    }
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": verifier.CONFIG,
                   "mediaType": "application/vnd.oci.image.config.v1+json"},
    }
    outputs = [
        verifier.INDEX + " linux amd64\n",
        json.dumps(index),
        json.dumps(manifest),
    ]
    def fake_run(argv, **kwargs):
        observed.append(argv)
        assert kwargs.get("check") is False
        assert kwargs.get("timeout") == 25
        assert kwargs.get("capture_output") is True
        return SimpleNamespace(returncode=0, stdout=outputs[len(observed) - 1], stderr="")
    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    assert verifier.verified_postgres_oci() is True
    assert observed == [
        ["docker", "--host=unix:///var/run/docker.sock", "image", "inspect",
         "--platform", "linux/amd64",
         verifier.REGISTRY_IMAGE + "@" + verifier.INDEX,
         "--format", "{{.Id}} {{.Os}} {{.Architecture}}"],
        ["docker", "buildx", "imagetools", "inspect",
         verifier.REGISTRY_IMAGE + "@" + verifier.INDEX, "--raw"],
        ["docker", "buildx", "imagetools", "inspect",
         verifier.REGISTRY_IMAGE + "@" + verifier.MANIFEST, "--raw"],
    ]
