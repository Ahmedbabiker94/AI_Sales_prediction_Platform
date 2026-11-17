"""Review-only fork test: synthetic run() exceptions, zero Docker calls.

The original run_postgres.py stays protected and unchanged. This is not
authorization to execute a real disposable database or merge the fork.
"""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres_security_patch_candidate")


@pytest.mark.parametrize("case", [
    "unexpected_secret", "json_failure", "evidence_write_failure",
    "signal_install_failure", "password_generation_failure",
])
def test_fork_restores_context_and_never_silently_passes(
        fork, monkeypatch, tmp_path, case):
    assert fork._LIFECYCLE.get() is None
    assert fork._TEST_SECRETS.get() == ()
    monkeypatch.setattr(fork, "verified_clean_source", lambda: "a" * 40)
    events = []
    original_handler = object()

    def set_handler(*args):
        events.append("install" if len(events) == 0 else "restore")
        if case == "signal_install_failure" and len(events) == 1:
            raise RuntimeError("synthetic installation error")

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: original_handler, signal=set_handler))
    clock = [0.0]
    def monotonic():
        clock[0] += 1.0
        return clock[0]
    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic, sleep=lambda _: None))

    if case == "password_generation_failure":
        monkeypatch.setattr(fork.secrets, "token_hex",
                            lambda _: (_ for _ in ()).throw(
                                RuntimeError("synthetic password error")))

    captured = {}
    def stop_before_docker(_):
        record = fork._LIFECYCLE.get()
        captured["run_id"] = record["run_id"]
        if case == "unexpected_secret":
            record["unexpected_metadata"] = {
                "custom_field": fork._TEST_SECRETS.get()[0]}
        if case == "json_failure":
            record["unexpected_metadata"] = object()
        raise RuntimeError("synthetic preflight, NO Docker")

    monkeypatch.setattr(fork, "inspect_image", stop_before_docker)
    monkeypatch.setattr(fork, "remove_owned", lambda *args: True)

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker"
        assert argv[1:] in (
            ["ps", "-aq", "--filter", "label=r7_05b3_run=" + str(captured["run_id"])],
            ["volume", "ls", "-q", "--filter", "label=r7_05b3_run=" + str(captured["run_id"])],
        )
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(fork, "command", fake_command)

    if case == "evidence_write_failure":
        def refused_write(*args):
            raise RuntimeError("synthetic evidence write failure")
        monkeypatch.setattr(fork, "write_evidence", refused_write)

    evidence = tmp_path / "evidence"
    try:
        if case == "unexpected_secret":
            expected = fork.CleanupUnconfirmed
        elif case == "json_failure":
            expected = TypeError
        else:
            expected = RuntimeError
        with pytest.raises(expected):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
        assert events.count("restore") == 1
        path = evidence / "run-manifest.json"
        if case == "unexpected_secret":
            obj = json.loads(path.read_text(encoding="utf-8"))
            assert obj["result"] == "FAILED"
            assert obj["cleanup_passed"] is False
            assert obj["cleanup_status"] == "CLEANUP_UNCONFIRMED"
            assert obj["secret_redactions"] >= 1
            assert obj["unexpected_metadata"]["custom_field"] == (
                "<redacted-disposable-secret>")
            assert "test-only-" not in path.read_text(encoding="utf-8")
        elif case not in ("password_generation_failure",
                         "signal_install_failure"):
            assert not path.exists() if case in ("json_failure",
                                                 "evidence_write_failure") else True
    finally:
        # Guard CI if fork regresses and leaves secrets bound.
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
