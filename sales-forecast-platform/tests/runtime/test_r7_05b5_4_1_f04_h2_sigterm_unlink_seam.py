"""F04-H2: deterministically inject fake SIGTERM at exact unlink call seam.

This is a candidate-only offline test. No operating-system signal is emitted,
no subprocess, Docker, PostgreSQL, network, or live credentials are touched.
The test-owned temporary envfile contains only a fabricated marker.

sys.settrace delivers the installed *mock* signal handler at the line where
the candidate is ABOUT to invoke remove_transient_credential_file, after
credential_cleanup_attempted was set to True.
"""
import ast
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


FORK_PATH = Path(__file__).resolve().parents[1] / "postgres" / "run_postgres_security_patch_candidate.py"


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(FORK_PATH.parent))
    runner = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    return runner


def _unlink_lines():
    tree = ast.parse(FORK_PATH.read_text(encoding="utf-8"))
    run = next(node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    result = sorted(node.lineno for node in ast.walk(run)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "remove_transient_credential_file")
    assert len(result) == 2
    return dict(normal=result[0], fallback=result[1])


@pytest.mark.parametrize("path_kind", ["normal", "fallback"])
@pytest.mark.parametrize("signal_count", [1, 2])
def test_sigterm_after_unlink_flag_does_not_bypass_owned_file_removal(
        fork, monkeypatch, tmp_path, path_kind, signal_count):
    evidence = tmp_path / "evidence"
    envfile = evidence / "test-client.env"
    handler_state = {"active": "original"}
    restores = []
    calls = []
    captured = {}
    delivered = []
    unlink_lines = _unlink_lines()
    targeted_line = unlink_lines[path_kind]

    def mock_signal(sig, handler):
        assert sig == 15
        handler_state["active"] = handler
        restores.append(handler)

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "original", signal=mock_signal))
    clock = [0.0]

    def monotonic():
        clock[0] += .1
        return clock[0]

    def interrupt_reconciliation(_):
        raise KeyboardInterrupt("synthetic reconciliation interruption")

    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic,
        sleep=interrupt_reconciliation if path_kind == "fallback"
        else lambda _: None))

    def preflight(_):
        record = fork._LIFECYCLE.get()
        assert record is not None
        captured["record"] = record
        envfile.write_text("synthetic-owned-only", encoding="utf-8")
        if path_kind == "fallback":
            record["creation_attempts"].append({
                "kind": "container",
                "name": "r7-05b3-" + record["run_hex"] + "-synthetic",
                "state": "DAEMON_UNRESOLVED",
            })
        raise RuntimeError("synthetic preflight, no Docker")

    monkeypatch.setattr(fork, "inspect_image", preflight)
    monkeypatch.setattr(fork, "remove_owned", lambda *args: True)
    # Normal final inventory only. Fallback must never reach a Docker command.
    if path_kind == "normal":
        monkeypatch.setattr(fork, "command",
                            lambda *a, **k: SimpleNamespace(stdout=""))

    actual_unlink = fork.remove_transient_credential_file

    def verified_unlink(path):
        assert path == envfile
        calls.append(path)
        return actual_unlink(path)

    monkeypatch.setattr(fork, "remove_transient_credential_file", verified_unlink)

    old_trace = sys.gettrace()
    def injected_trace(frame, event, arg):
        if (event == "line"
                and frame.f_code.co_name == "run"
                and frame.f_code.co_filename == str(FORK_PATH)
                and frame.f_lineno == targeted_line
                and not delivered):
            # The exact seam between the boolean attempt flag and helper entry.
            assert frame.f_locals["credential_cleanup_attempted"] is True
            assert frame.f_locals["deferred_term"]["active"] is True
            assert calls == []
            handler = handler_state["active"]
            assert callable(handler)
            for _ in range(signal_count):
                handler(15, None)  # fake invocation; NEVER os.kill
                delivered.append(15)
            assert frame.f_locals["deferred_term"]["pending"] is True
        return injected_trace

    try:
        sys.settrace(injected_trace)
        with pytest.raises(KeyboardInterrupt, match="deferred SIGTERM"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    finally:
        sys.settrace(old_trace)

    assert delivered == [15] * signal_count
    assert calls == [envfile]
    assert not envfile.exists()
    assert not (evidence / "run-manifest.json").exists()
    assert captured["record"]["credential_file_cleanup"] == "REMOVED"
    assert captured["record"]["result"] == "FAILED"
    assert captured["record"]["cleanup_passed"] is False
    assert captured["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert len(restores) == 2
    assert handler_state["active"] == "original"
    assert fork._LIFECYCLE.get() is None
    assert fork._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("path_kind", ["normal", "fallback"])
def test_sigterm_injected_inside_unlink_is_deferred_until_after_helper(
        fork, monkeypatch, tmp_path, path_kind):
    evidence = tmp_path / "evidence"
    envfile = evidence / "test-client.env"
    seen = {"record": None, "handler": None, "removals": 0}
    signal_calls = []

    def signal_fake(_, handler):
        signal_calls.append(handler)
        seen["handler"] = handler

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old", signal=signal_fake))
    tick = [0.0]

    def monotonic():
        tick[0] += .1
        return tick[0]

    monkeypatch.setattr(fork, "time", SimpleNamespace(
        monotonic=monotonic,
        sleep=lambda _: (_ for _ in ()).throw(
            KeyboardInterrupt("synthetic cleanup stop"))
        if path_kind == "fallback" else lambda _: None))

    def preflight(_):
        record = fork._LIFECYCLE.get()
        seen["record"] = record
        envfile.write_text("synthetic-only", encoding="utf-8")
        if path_kind == "fallback":
            record["creation_attempts"].append({
                "kind": "container",
                "name": "r7-05b3-" + record["run_hex"] + "-fake",
                "state": "DAEMON_UNRESOLVED",
            })
        raise RuntimeError("synthetic preflight")

    monkeypatch.setattr(fork, "inspect_image", preflight)
    monkeypatch.setattr(fork, "remove_owned", lambda *a: True)
    if path_kind == "normal":
        monkeypatch.setattr(fork, "command",
                            lambda *a, **k: SimpleNamespace(stdout=""))

    real_unlink = fork.remove_transient_credential_file
    def signal_inside_helper(path):
        seen["removals"] += 1
        # Fake SIGTERM raised *while* normal Python cleanup callback executes.
        seen["handler"](15, None)
        assert path.exists()
        return real_unlink(path)
    monkeypatch.setattr(fork, "remove_transient_credential_file", signal_inside_helper)

    with pytest.raises(KeyboardInterrupt, match="deferred SIGTERM"):
        fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert seen["removals"] == 1
    assert not envfile.exists()
    assert seen["record"]["credential_file_cleanup"] == "REMOVED"
    assert seen["record"]["result"] == "FAILED"
    assert seen["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert signal_calls[-1] == "old"
    assert fork._LIFECYCLE.get() is None
    assert fork._TEST_SECRETS.get() == ()
