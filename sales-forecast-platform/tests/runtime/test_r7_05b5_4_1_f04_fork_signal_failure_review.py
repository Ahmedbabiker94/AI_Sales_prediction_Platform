"""F-04 patched-fork fault injection, entirely offline.

Tests only the candidate fork with synthetic signal callbacks. In particular,
restoration failure must never prevent lifecycle and secret token resets.
No real signal handler mutations, Docker, PostgreSQL, or network calls.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def review(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    fork = importlib.import_module("run_postgres_security_patch_candidate")
    assert fork._LIFECYCLE.get() is None
    assert fork._TEST_SECRETS.get() == ()
    monkeypatch.setattr(fork, "verified_clean_source", lambda: "a" * 40)
    return fork


@pytest.mark.parametrize("case", [
    "getsignal_failure",
    "partial_install_and_restore_failure",
    "restore_failure_after_secrets_bound",
    "nested_context_preservation",
])
def test_fork_restores_contexts_even_on_signal_failures(
        review, monkeypatch, tmp_path, case):
    fork = review
    previous = object()
    signals = []

    def getsignal(_):
        if case == "getsignal_failure":
            raise RuntimeError("synthetic getsignal failure")
        return previous

    def set_handler(_, handler):
        signals.append(handler)
        if case == "partial_install_and_restore_failure":
            raise RuntimeError("synthetic signal failure")
        if case == "restore_failure_after_secrets_bound" and handler is previous:
            raise RuntimeError("synthetic restore failure")

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=getsignal, signal=set_handler))
    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=lambda: 0.0, sleep=lambda _: None))
    seen = {}

    def pre_docker_stop(_):
        seen["id"] = fork._LIFECYCLE.get()["run_id"]
        raise RuntimeError("synthetic preflight; Docker never called")

    monkeypatch.setattr(fork, "inspect_image", pre_docker_stop)
    monkeypatch.setattr(fork, "remove_owned", lambda *args: True)

    def fake_command(args, **kwargs):
        assert args[0] == "docker"
        assert args[1:] in (
            ["ps", "-aq", "--filter", "label=r7_05b3_run=" + str(seen["id"])],
            ["volume", "ls", "-q", "--filter", "label=r7_05b3_run=" + str(seen["id"])],
        )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(fork, "command", fake_command)
    if case == "nested_context_preservation":
        first = fork._LIFECYCLE.set({"outer": True})
        second = fork._TEST_SECRETS.set(("outer-token",))
    else:
        first = second = None
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=tmp_path/"evidence")
        if first is None:
            assert fork._LIFECYCLE.get() is None
            assert fork._TEST_SECRETS.get() == ()
        else:
            assert fork._LIFECYCLE.get() == {"outer": True}
            assert fork._TEST_SECRETS.get() == ("outer-token",)
        if case == "getsignal_failure":
            assert signals == []
        elif case == "partial_install_and_restore_failure":
            assert len(signals) == 2
            assert signals[1] is previous
        else:
            assert len(signals) == 2
            assert signals[1] is previous
    finally:
        if second is not None:
            fork._TEST_SECRETS.reset(second)
        if first is not None:
            fork._LIFECYCLE.reset(first)
        # Last resort cleanup of only the synthetic context in case of defect.
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
