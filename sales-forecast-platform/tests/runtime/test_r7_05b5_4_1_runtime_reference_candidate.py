"""Offline F-02 candidate contracts; no Docker, PostgreSQL or registry."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("runtime_reference_candidate")


def test_postgres_both_stages_use_exact_index_pin(candidate):
    refs = candidate.postgres_run_references(candidate.PG_REF)
    assert refs == {"provision": candidate.PG_REF, "server": candidate.PG_REF}
    assert refs["server"].startswith("docker.io/library/postgres@sha256:")


@pytest.mark.parametrize("bad", [
    "", None, "postgres:16.15", "sha256:" + "0"*64,
    "docker.io/library/postgres@sha256:" + "0"*64,
    "docker.io/library/python@sha256:" + "0"*64,
])
def test_never_accept_unverified_postgres_reference(candidate, bad):
    with pytest.raises(ValueError, match="not verified"):
        candidate.postgres_run_references(bad)


def test_valid_local_build_identity(candidate):
    iid = "sha256:" + "a"*64
    assert candidate.local_build_identity(
        iid + "\n", {"Id": iid, "Os": "linux", "Architecture": "amd64"}) == iid


@pytest.mark.parametrize("iid,info", [
    ("", {"Id": "sha256:" + "a"*64, "Os": "linux", "Architecture": "amd64"}),
    ("sha256:bad", {"Id": "sha256:" + "a"*64, "Os": "linux", "Architecture": "amd64"}),
    ("sha256:" + "a"*64, {"Id": "sha256:" + "b"*64, "Os": "linux", "Architecture": "amd64"}),
    ("sha256:" + "a"*64, {"Id": "sha256:" + "a"*64, "Os": "linux", "Architecture": "arm64"}),
    ("sha256:" + "a"*64, {"Id": "sha256:" + "a"*64, "Os": "windows", "Architecture": "amd64"}),
    ("sha256:" + "a"*64, {}),
    ("sha256:" + "a"*64, None),
])
def test_invalid_build_result_is_rejected(candidate, iid, info):
    with pytest.raises(ValueError):
        candidate.local_build_identity(iid, info)


def test_no_original_runner_integration(candidate):
    """Existing approved runner has not been wired to the candidate helpers."""
    runner = importlib.import_module("run_postgres")
    assert runner.POSTGRES.startswith("sha256:")
    assert candidate.PG_REF.startswith("docker.io/library/postgres@sha256:")
