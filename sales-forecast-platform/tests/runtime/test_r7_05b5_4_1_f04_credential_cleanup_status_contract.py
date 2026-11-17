"""F-04 review-fork contract: exception/unknown unlink is never cleanup success.

Candidate-only; zero external subprocesses/Docker/PG or credential bytes.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    r = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(r, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(r, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "prior", signal=lambda *a: None))
    monkeypatch.setattr(r, "time", SimpleNamespace(
        monotonic=lambda: 0.0, sleep=lambda _: None))
    return r


@pytest.mark.parametrize("outcome", ["UNKNOWN", None, "", "UNCONFIRMED", "RAISE"])
def test_normal_unlink_error_or_unexpected_status_rejected_once(
        fork, monkeypatch, tmp_path, outcome):
    seen = {}
    def stop_before_docker(_):
        seen["record"] = fork._LIFECYCLE.get()
        raise RuntimeError("synthetic preflight")
    monkeypatch.setattr(fork, "inspect_image", stop_before_docker)
    monkeypatch.setattr(fork, "command", lambda *a, **k: SimpleNamespace(stdout=""))
    attempts = []
    def unlink(path):
        attempts.append(path)
        if outcome == "RAISE":
            raise KeyboardInterrupt("synthetic unlink interrupted")
        return outcome
    monkeypatch.setattr(fork, "remove_transient_credential_file", unlink)
    evidence = tmp_path / "evidence"
    try:
        if outcome == "RAISE":
            with pytest.raises(KeyboardInterrupt, match="synthetic unlink interrupted"):
                fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        else:
            with pytest.raises(RuntimeError, match="synthetic preflight"):
                fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert attempts == [evidence / "test-client.env"]
        assert seen["record"]["result"] == "FAILED"
        assert seen["record"]["credential_file_cleanup"] == "UNCONFIRMED"
        assert seen["record"]["cleanup_passed"] is False
        assert seen["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
        if outcome == "RAISE":
            assert not (evidence / "run-manifest.json").exists()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())


@pytest.mark.parametrize("outcome", ["UNKNOWN", "NOT_PRESENT", "REMOVED"])
def test_interrupted_reconciliation_fallback_normalizes_status(
        fork, monkeypatch, tmp_path, outcome):
    tick = [0.0]
    def monotonic():
        tick[0] += 0.1
        return tick[0]
    def interrupted_sleep(_):
        raise KeyboardInterrupt("synthetic reconciliation interrupt")
    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic, sleep=interrupted_sleep))
    seen = {}
    def preflight(_):
        record = fork._LIFECYCLE.get()
        seen["record"] = record
        record["creation_attempts"].append({
            "kind": "container",
            "name": "r7-05b3-" + record["run_hex"] + "-synthetic",
            "state": "DAEMON_UNRESOLVED",
        })
        raise RuntimeError("synthetic preflight")
    monkeypatch.setattr(fork, "inspect_image", preflight)
    monkeypatch.setattr(fork, "remove_owned", lambda *a: True)
    calls = []
    monkeypatch.setattr(fork, "remove_transient_credential_file",
                        lambda path: calls.append(path) or outcome)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic reconciliation interrupt"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert calls == [evidence / "test-client.env"]
        expected = outcome if outcome in ("REMOVED", "NOT_PRESENT") else "UNCONFIRMED"
        assert seen["record"]["credential_file_cleanup"] == expected
        if expected == "UNCONFIRMED":
            assert seen["record"]["result"] == "FAILED"
            assert seen["record"]["cleanup_passed"] is False
            assert seen["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
