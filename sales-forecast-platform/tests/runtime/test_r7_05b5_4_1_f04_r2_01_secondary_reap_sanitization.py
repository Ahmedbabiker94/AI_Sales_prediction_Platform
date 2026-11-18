"""F04-R2-01 HIGH: no raw credential-bearing timeout escapes secondary reaping.

Review-fork test only. The on-disk command() guard stays in place. A single
function is extracted from a copied AST in memory with only its first,
unconditional review-only guard removed. Popen/poll/killpg/communicate are
fully fake; no subprocess, Docker, PostgreSQL or OS signal is invoked.
"""
import ast
import copy
import importlib
from pathlib import Path
import subprocess
import traceback
from types import SimpleNamespace

import pytest


SECRET = "test-only-R2-01-SYNTHETIC-SECRET-NOT-A-REAL-CREDENTIAL"
FORK_PATH = Path(__file__).resolve().parents[1] / "postgres" / "run_postgres_security_patch_candidate.py"


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(FORK_PATH.parent))
    return importlib.import_module("run_postgres_security_patch_candidate")


CASES = [
    ("poll_oserror", RuntimeError, "POLL_EXCEPTION", False),
    ("poll_keyboard_interrupt", KeyboardInterrupt, "POLL_EXCEPTION", False),
    ("kill_oserror", RuntimeError, "KILLPG_EXCEPTION", True),
    ("kill_keyboard_interrupt", KeyboardInterrupt, "KILLPG_EXCEPTION", True),
    ("reap_decode_error", RuntimeError, "WAIT_EXCEPTION", True),
    ("reap_oserror", RuntimeError, "WAIT_EXCEPTION", True),
    ("reap_keyboard_interrupt", KeyboardInterrupt, "WAIT_EXCEPTION", True),
    ("reap_system_exit", SystemExit, "WAIT_EXCEPTION", True),
]


def fake_command(fork, scenario):
    source = ast.parse(FORK_PATH.read_text(encoding="utf-8"))
    original = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef) and node.name == "command")
    assert isinstance(original.body[0], ast.Raise)
    assert isinstance(original.body[0].exc, ast.Call)
    assert original.body[0].exc.func.id == "RuntimeError"
    extract = copy.deepcopy(original)
    extract.body.pop(0)
    observed = {"kills": [], "process": None}

    class MockPopen:
        pid = 44551

        def __init__(self, argv, **kwargs):
            observed["process"] = self
            self.argv = list(argv)
            self.returncode = None
            self.communicates = 0

        def poll(self):
            if scenario.startswith("poll_"):
                if scenario.endswith("keyboard_interrupt"):
                    raise KeyboardInterrupt("synthetic poll interrupt")
                raise OSError("synthetic poll failure")
            return self.returncode

        def communicate(self, input=None, timeout=None):
            self.communicates += 1
            if self.communicates == 1:
                raise subprocess.TimeoutExpired(
                    cmd=self.argv, timeout=timeout,
                    output="stdout " + SECRET,
                    stderr="stderr " + SECRET)
            if scenario == "reap_decode_error":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid synthetic output")
            if scenario == "reap_oserror":
                raise OSError("synthetic wait failure")
            if scenario == "reap_keyboard_interrupt":
                raise KeyboardInterrupt("synthetic wait interrupt")
            if scenario == "reap_system_exit":
                raise SystemExit("synthetic wait terminated")
            self.returncode = -9
            return ("synthetic", "")

    def fake_killpg(pid, signum):
        observed["kills"].append((pid, signum))
        if scenario.startswith("kill_"):
            if scenario.endswith("keyboard_interrupt"):
                raise KeyboardInterrupt("synthetic kill interrupt")
            raise OSError("synthetic kill failure")

    namespace = {
        "subprocess": SimpleNamespace(
            Popen=MockPopen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
            CompletedProcess=subprocess.CompletedProcess,
            TimeoutExpired=subprocess.TimeoutExpired),
        "os": SimpleNamespace(environ={}, killpg=fake_killpg),
        "time": SimpleNamespace(monotonic=lambda: 10.0, time_ns=lambda: 321),
        "signal": SimpleNamespace(SIGKILL=9),
        "_LIFECYCLE": fork._LIFECYCLE,
        "redact_test_secrets": fork.redact_test_secrets,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[extract], type_ignores=[])), str(FORK_PATH), "exec"), namespace)
    return namespace["command"], observed


@pytest.mark.parametrize("scenario,expected,issue,kill_expected", CASES)
def test_secondary_reap_fault_never_exposes_original_timeout(
        fork, scenario, expected, issue, kill_expected):
    command, observed = fake_command(fork, scenario)
    record = {"run_hex": "abc123", "creation_attempts": []}
    token = fork._LIFECYCLE.set(record)
    args = ["docker", "run", "--name", "r7-05b3-abc123-synthetic",
            "-e", "PGPASSWORD=" + SECRET, "sha256:synthetic"]
    try:
        with pytest.raises(expected, match="details suppressed") as captured:
            command(args, timeout=2)
        public = captured.value
        assert public.__context__ is None
        assert public.__cause__ is None
        assert not hasattr(public, "cmd")
        assert not hasattr(public, "output")
        assert not hasattr(public, "stderr")
        displayed = [
            str(public), repr(public), repr(public.args),
            repr(vars(public)), "".join(traceback.format_exception(public)),
        ]
        assert all(SECRET not in item for item in displayed)
        assert all("PGPASSWORD" not in item for item in displayed)
        attempts = record["creation_attempts"]
        assert len(attempts) == 1
        assert attempts[0]["state"] == "DAEMON_UNRESOLVED"
        assert issue in attempts[0].get("local_reap_issues", [])
        assert type(attempts[0].get("local_reaped")) is bool
        if scenario.startswith("reap_"):
            assert attempts[0]["local_reaped"] is False, (
                "failed communicate cannot prove local wait completed")
        if scenario.startswith("poll_"):
            assert attempts[0]["local_reaped"] is True, (
                "successful bounded second communicate confirms local reap")
        assert bool(observed["kills"]) is kill_expected
        assert observed["process"].communicates == 2
    finally:
        fork._LIFECYCLE.reset(token)
    assert fork._LIFECYCLE.get() is None


def test_default_review_only_command_remains_unconditionally_blocked(fork):
    with pytest.raises(RuntimeError, match="OFFLINE_REVIEW_ONLY"):
        fork.command(["docker", "run", "-e", "PGPASSWORD=" + SECRET])
