"""F04-M2 negative regression: reconciliation interrupts invalidate in-memory PASS.

This test covers a fully emulated successful main body followed by synthetic
KeyboardInterrupt at container deletion, volume deletion, or reconciliation
sleep. It never starts Docker, PostgreSQL, a real subprocess, or OS signals.
The frozen accepted runner is neither imported nor modified.
"""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    runner = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(runner, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(runner, "build_client",
                        lambda wheelhouse, evidence: "sha256:synthetic")
    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    return runner


def install_fake_success_commands(fork, monkeypatch):
    calls = []

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "a real command must never run"
        calls.append(tuple(argv))
        if argv[1:3] == ["volume", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such volume")
        if argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps([{
                "Config": {"Env": []}
            }]), stderr="")
        if argv[1] == "exec" and "/proc/1/comm" in argv:
            return SimpleNamespace(returncode=0, stdout="postgres\n", stderr="")
        if argv[1] == "exec":
            return SimpleNamespace(returncode=0, stdout="same-cluster-id", stderr="")
        if argv[1] in ("ps", "volume") and "ls" in argv or argv[1] == "ps":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="synthetic tests passed", stderr="")

    monkeypatch.setattr(fork, "command", fake_command)
    return calls


@pytest.mark.parametrize("interrupt_at", ["container", "volume", "sleep"])
def test_successful_body_then_interrupted_reconciliation_cannot_keep_pass(
        fork, monkeypatch, tmp_path, interrupt_at):
    commands = install_fake_success_commands(fork, monkeypatch)
    captured = {}
    signal_calls = []
    handler_state = {"current": "previous"}

    def fake_signal(signum, handler):
        assert signum == 15
        signal_calls.append(handler)
        handler_state["current"] = handler

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "previous", signal=fake_signal))

    def success_stage(**kwargs):
        captured["record"] = kwargs["record"]
        captured["envfile"] = kwargs["envfile"]
        assert captured["envfile"].exists()
        if interrupt_at == "sleep":
            # Only bounded cleanup sees this unresolved synthetic mutation.
            record = kwargs["record"]
            record["creation_attempts"].append({
                "kind": "container",
                "name": "r7-05b3-" + record["run_hex"] + "-mock-late",
                "state": "DAEMON_UNRESOLVED",
            })

    unlink_calls = []
    real_unlink = fork.remove_transient_credential_file

    def verified_unlink(path):
        unlink_calls.append(path)
        return real_unlink(path)

    monkeypatch.setattr(fork, "remove_transient_credential_file", verified_unlink)

    def fake_remove(kind, name, owner):
        if interrupt_at == kind:
            assert "record" in captured, "the successful main body must finish first"
            raise KeyboardInterrupt("synthetic " + kind + " reconciliation interrupt")
        return True

    monkeypatch.setattr(fork, "remove_owned", fake_remove)

    if interrupt_at == "sleep":
        def interrupted_sleep(_):
            assert "record" in captured, "the successful main body must finish first"
            raise KeyboardInterrupt("synthetic sleep reconciliation interrupt")
        monkeypatch.setattr(fork, "time", SimpleNamespace(
            monotonic=lambda: 10.0, sleep=interrupted_sleep))

    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic .* reconciliation interrupt"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                     _host_stage=success_stage)

        assert commands, "the mock must have exercised the full successful main body"
        assert captured["record"]["result"] == "FAILED"
        assert captured["record"]["cleanup_passed"] is False
        assert captured["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert captured["record"]["credential_file_cleanup"] == "REMOVED"
        assert unlink_calls == [captured["envfile"]]
        assert not captured["envfile"].exists()
        assert not (evidence / "run-manifest.json").exists()
        assert len(signal_calls) == 2, "the previous SIGTERM handler restored once"
        assert handler_state["current"] == "previous"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
