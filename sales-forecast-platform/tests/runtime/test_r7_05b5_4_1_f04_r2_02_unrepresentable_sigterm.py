"""F04-R2-02: a SIGTERM handler that cannot be represented must fail closed.

All hooks are mocked. In particular, signal.getsignal/signal.signal are
in-memory fakes; no actual OS signals, Docker, PG or subprocesses are used.
The accepted frozen runner is never imported or changed.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(module, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    return module


@pytest.mark.parametrize("nested_context", [False, True])
def test_none_previous_sigterm_refuses_installation_and_pass(
        fork, monkeypatch, tmp_path, nested_context):
    """None is a valid getsignal result but cannot be restored by signal.signal."""
    signal_calls = []
    observed = {"file_cleanup_calls": 0, "docker_calls": 0}
    # A mistakenly installed callback is observable; never emit a real signal.
    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15,
        getsignal=lambda signum: None,
        signal=lambda signum, handler: signal_calls.append((signum, handler))))
    def fail_docker(*args, **kwargs):
        observed["docker_calls"] += 1
        pytest.fail("must reject unrepresentable SIGTERM before Docker/preflight")
    monkeypatch.setattr(fork, "inspect_image", fail_docker)
    monkeypatch.setattr(fork, "build_client", fail_docker)
    monkeypatch.setattr(fork, "command", fail_docker)
    def capture_file_cleanup(path):
        observed["file_cleanup_calls"] += 1
        observed["record"] = fork._LIFECYCLE.get()
        assert observed["record"] is not None
        assert path.name == "test-client.env"
        assert not path.exists()
        return "NOT_PRESENT"
    monkeypatch.setattr(fork, "remove_transient_credential_file",
                        capture_file_cleanup)
    outer_record = {"outer": "R2-02"} if nested_context else None
    outer_secrets = ("outer-secret-sentinel",) if nested_context else ()
    outer_lifecycle_token = fork._LIFECYCLE.set(outer_record)
    outer_secret_token = fork._TEST_SECRETS.set(outer_secrets)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(fork.CleanupUnconfirmed,
                           match="SIGTERM prior handler unrepresentable"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert signal_calls == [], "must not install an un-restorable handler"
        assert observed["docker_calls"] == 0
        assert observed["file_cleanup_calls"] == 1
        record = observed["record"]
        assert record["result"] == "FAILED"
        assert record["cleanup_passed"] is False
        assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert record["handler_restoration"] == "UNCONFIRMED"
        assert record["credential_file_cleanup"] == "NOT_PRESENT"
        assert not (evidence / "run-manifest.json").exists()
        assert not (evidence / "test-client.env").exists()
        assert fork._LIFECYCLE.get() is outer_record
        assert fork._TEST_SECRETS.get() == outer_secrets
    finally:
        fork._TEST_SECRETS.reset(outer_secret_token)
        fork._LIFECYCLE.reset(outer_lifecycle_token)


def test_representable_sigterm_default_handler_remains_restorable(
        fork, monkeypatch, tmp_path):
    """0 (SIG_DFL) is valid, so rejecting all falsey handlers would regress."""
    signal_calls = []
    def set_handler(signum, handler):
        signal_calls.append((signum, handler))
    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, SIG_DFL=0, getsignal=lambda _: 0,
        signal=set_handler))
    observed = {}
    def synthetic_preflight(_):
        observed["record"] = fork._LIFECYCLE.get()
        raise RuntimeError("synthetic preflight; no Docker operation")
    monkeypatch.setattr(fork, "inspect_image", synthetic_preflight)
    monkeypatch.setattr(fork, "command",
                        lambda *a, **kw: SimpleNamespace(
                            stdout="", stderr="", returncode=0))
    evidence = tmp_path / "evidence"
    with pytest.raises(RuntimeError, match="synthetic preflight"):
        fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert len(signal_calls) == 2
    assert callable(signal_calls[0][1])
    assert signal_calls[1] == (15, 0)
    assert observed["record"]["handler_restoration"] == "RESTORED"
    assert observed["record"]["result"] == "FAILED"
    assert fork._LIFECYCLE.get() is None
    assert fork._TEST_SECRETS.get() == ()
