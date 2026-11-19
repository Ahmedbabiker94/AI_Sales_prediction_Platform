"""Bounded ownership-only credential cleanup fallback, mock-only.

No subprocess, actual Docker/PG, disk credentials, or production mounts.
Ensure exceptional cleanup attempts only the known evidence/test-client.env
and never marks the outcome as accepted.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    model = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(model, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(model, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old", signal=lambda *a: None))
    return model


@pytest.mark.parametrize("outcome", ["REMOVED", "UNCONFIRMED", "RAISE"])
def test_interrupted_cleanup_attempts_only_own_envfile_once_and_never_passes(
        fork, monkeypatch, tmp_path, outcome):
    clock = [0.0]
    def monotonic():
        clock[0] += 0.1
        return clock[0]

    def stop_sleep(_):
        raise KeyboardInterrupt("synthetic reconciliation interruption")

    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic, sleep=stop_sleep))
    observed = {}
    def stop_before_docker(_):
        record = fork._LIFECYCLE.get()
        observed["record"] = record
        record["creation_attempts"].append({
            "kind": "container",
            "name": "r7-05b3-" + record["run_hex"] + "-synthetic",
            "state": "DAEMON_UNRESOLVED",
        })
        raise RuntimeError("synthetic preflight")
    monkeypatch.setattr(fork, "inspect_image", stop_before_docker)
    monkeypatch.setattr(fork, "remove_owned", lambda *a: True)
    attempts = []
    def unlink(path):
        attempts.append(path)
        if outcome == "RAISE":
            raise KeyboardInterrupt("synthetic unlink error")
        return outcome
    monkeypatch.setattr(fork, "remove_transient_credential_file", unlink)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic reconciliation"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert attempts == [evidence / "test-client.env"]
        assert observed["record"]["result"] == "FAILED"
        if outcome != "REMOVED":
            assert observed["record"]["credential_file_cleanup"] == "UNCONFIRMED"
            assert observed["record"]["cleanup_passed"] is False
            assert observed["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
        assert not (evidence / "run-manifest.json").exists()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())


def test_normal_finalization_does_not_duplicate_unlink(fork, monkeypatch, tmp_path):
    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=lambda: 0.0, sleep=lambda _: None))
    def stop_before_docker(_):
        raise RuntimeError("synthetic preflight")
    monkeypatch.setattr(fork, "inspect_image", stop_before_docker)
    observed = {}
    monkeypatch.setattr(fork, "command", lambda *a, **k: SimpleNamespace(stdout=""))
    attempts = []
    def unlink(path):
        attempts.append(path)
        return "NOT_PRESENT"
    monkeypatch.setattr(fork, "remove_transient_credential_file", unlink)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(RuntimeError, match="synthetic preflight"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert attempts == [evidence / "test-client.env"]
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
