"""F04-H1 candidate-only subprocess timeout adversarial contract.

The review fork's normal command() stays hard-stopped. For behavioral review,
this test removes ONLY that first guard in a copy of its function AST IN MEMORY,
then executes the remainder with a fake Popen and fake os.killpg. No Docker,
real subprocess, PostgreSQL, network, or actual credentials are ever used.
"""
import ast
import copy
import importlib
from pathlib import Path
import subprocess
import traceback
from types import SimpleNamespace

import pytest


SECRET = "test-only-SYNTHETIC-CREDENTIAL-NOT-REAL"
FORK_PATH = Path(__file__).resolve().parents[1] / "postgres" / "run_postgres_security_patch_candidate.py"


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(FORK_PATH.parent))
    return importlib.import_module("run_postgres_security_patch_candidate")


def offline_command(fork, scenario):
    tree = ast.parse(FORK_PATH.read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "command")
    # Guard is retained on disk and MUST remain the first statement.
    assert isinstance(function.body[0], ast.Raise)
    assert isinstance(function.body[0].exc, ast.Call)
    assert isinstance(function.body[0].exc.func, ast.Name)
    assert function.body[0].exc.func.id == "RuntimeError"
    model = copy.deepcopy(function)
    model.body.pop(0)
    seen = {"spawned": [], "killed": [], "processes": []}

    class FakeProcess:
        pid = 32123
        def __init__(self, argv, **kwargs):
            self.argv = list(argv)
            self.returncode = None
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                if scenario == "success":
                    self.returncode = 0
                    return "ready", ""
                raise subprocess.TimeoutExpired(
                    cmd=self.argv, timeout=timeout,
                    output="partial " + SECRET, stderr="stderr " + SECRET)
            if scenario == "second-timeout":
                raise subprocess.TimeoutExpired(
                    cmd=self.argv, timeout=timeout,
                    stderr="reaper " + SECRET)
            return "", ""

        def poll(self):
            return self.returncode

    def fake_popen(argv, **kwargs):
        seen["spawned"].append((list(argv), kwargs))
        if scenario == "spawn-timeout":
            raise subprocess.TimeoutExpired(
                cmd=argv, timeout=2, stderr=SECRET)
        proc = FakeProcess(argv, **kwargs)
        seen["processes"].append(proc)
        return proc

    def fake_killpg(pid, sig):
        seen["killed"].append((pid, sig))
        for proc in seen["processes"]:
            if proc.pid == pid:
                proc.returncode = -9

    safe_namespace = {
        "subprocess": SimpleNamespace(
            Popen=fake_popen, PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
            CompletedProcess=subprocess.CompletedProcess,
            TimeoutExpired=subprocess.TimeoutExpired),
        "os": SimpleNamespace(environ={}, killpg=fake_killpg),
        "time": SimpleNamespace(monotonic=lambda: 1.0),
        "signal": SimpleNamespace(SIGKILL=9),
        "_LIFECYCLE": fork._LIFECYCLE,
        "redact_test_secrets": fork.redact_test_secrets,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[model], type_ignores=[])), str(FORK_PATH), "exec"), safe_namespace)
    return safe_namespace["command"], seen


@pytest.mark.parametrize("scenario", ["timeout", "second-timeout", "spawn-timeout"])
def test_timeout_public_exception_has_no_raw_argv_output_or_chains(
        fork, scenario):
    command, seen = offline_command(fork, scenario)
    args = ["docker", "exec", "-e", "PGPASSWORD=" + SECRET, "synthetic",
            "psql", "--no-password"]
    with pytest.raises(RuntimeError, match="command details suppressed") as caught:
        command(args, timeout=2)
    public = caught.value
    texts = [
        str(public), repr(public), str(public.args),
        repr(vars(public)), "".join(traceback.format_exception(public)),
    ]
    assert all(SECRET not in text for text in texts)
    assert all("PGPASSWORD" not in text for text in texts)
    assert public.__context__ is None
    assert public.__cause__ is None
    assert not hasattr(public, "cmd")
    assert not hasattr(public, "output")
    assert not hasattr(public, "stderr")
    assert len(seen["spawned"]) == 1
    if scenario == "spawn-timeout":
        assert not seen["killed"]
    else:
        assert seen["killed"] == [(32123, 9)]


def test_mutation_timeout_stays_daemon_unresolved_and_tracks_local_reap(fork):
    command, seen = offline_command(fork, "timeout")
    record = {"run_hex": "abc123", "creation_attempts": []}
    token = fork._LIFECYCLE.set(record)
    try:
        with pytest.raises(RuntimeError, match="command details suppressed") as caught:
            command(["docker", "run", "--name", "r7-05b3-abc123-synthetic",
                     "-e", "PGPASSWORD=" + SECRET, "sha256:fake"], timeout=2)
        assert caught.value.__context__ is None
        assert len(record["creation_attempts"]) == 1
        attempt = record["creation_attempts"][0]
        assert attempt["state"] == "DAEMON_UNRESOLVED"
        assert attempt["local_reaped"] is True
        assert attempt["name"] == "r7-05b3-abc123-synthetic"
        assert seen["killed"] == [(32123, 9)]
    finally:
        fork._LIFECYCLE.reset(token)
    assert fork._LIFECYCLE.get() is None


def test_successful_fake_process_keeps_result_and_read_only_guard(fork):
    command, seen = offline_command(fork, "success")
    result = command(["docker", "image", "inspect", "sha256:fake"], timeout=2)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert result.stdout == "ready"
    assert seen["killed"] == []
    with pytest.raises(RuntimeError, match="OFFLINE_REVIEW_ONLY"):
        fork.command(["docker", "version"])
