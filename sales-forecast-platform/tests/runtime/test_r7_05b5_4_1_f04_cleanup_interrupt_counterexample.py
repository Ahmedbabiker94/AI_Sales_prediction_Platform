"""Candidate-only negative regression for interrupted reconciliation.

An interruption must attempt owned transient credential cleanup before exit.
No real Docker, PostgreSQL, command execution, or sensitive values.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    runner = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    return runner


def test_keyboard_interrupt_during_ambiguous_reconciliation_attempts_credential_cleanup(
        fork, monkeypatch, tmp_path):
    # Repair criterion: no exceptional path silently skips unlink attempt.
    signal_calls = []
    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "prior",
        signal=lambda *a: signal_calls.append(a)))
    clock = [0.0]

    def monotonic():
        clock[0] += 0.1
        return clock[0]

    def interrupted_sleep(_):
        raise KeyboardInterrupt("synthetic reconciliation interrupted")

    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic, sleep=interrupted_sleep))
    captured = {}

    def simulated_preflight(_):
        active = fork._LIFECYCLE.get()
        assert active is not None
        captured["run_id"] = active["run_id"]
        active["creation_attempts"].append({
            "kind": "container",
            "name": "r7-05b3-" + active["run_hex"] + "-fake",
            "state": "DAEMON_UNRESOLVED",
        })
        raise RuntimeError("synthetic preflight stop; zero Docker calls")

    monkeypatch.setattr(fork, "inspect_image", simulated_preflight)
    removed = []
    monkeypatch.setattr(fork, "remove_owned",
                        lambda kind, name, run_id: removed.append(name) or True)
    credential_removals = []
    monkeypatch.setattr(fork, "remove_transient_credential_file",
                        lambda path: credential_removals.append(path) or "REMOVED")

    # The command hard-stop in the candidate remains unmocked. If a real
    # command is reached, this test fails safely before subprocess.
    path = tmp_path / "evidence"
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic reconciliation"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=path)
        assert removed
        assert credential_removals == [path / "test-client.env"]
        assert not (path / "run-manifest.json").exists()
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
        assert len(signal_calls) == 2
    finally:
        # Contain test-only context even if fork regresses.
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
