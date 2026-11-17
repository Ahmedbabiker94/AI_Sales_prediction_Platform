"""Review fork must refuse all real command execution by default.

These tests NEVER invoke the accepted runner; no Docker, Git subprocess or
PostgreSQL is launched. Mocked tests of the fork are separate and explicit.
"""
import ast
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres_security_patch_candidate")


@pytest.mark.parametrize("args", [
    ["docker", "version"],
    ["docker", "run", "--name", "do-not-run", "sha256:" + "1"*64],
    ["git", "status"],
])
def test_default_command_never_invokes_subprocess(fork, monkeypatch, args):
    attempted = []
    def forbidden(*a, **kwargs):
        attempted.append((a, kwargs))
        raise AssertionError("subprocess execution forbidden")
    monkeypatch.setattr(fork.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="OFFLINE_REVIEW_ONLY"):
        fork.command(args)
    assert attempted == []


def test_candidate_run_fails_before_evidence_creation_or_external_command(
        fork, monkeypatch, tmp_path):
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append(args)
        raise AssertionError("external subprocess unexpectedly invoked")
    monkeypatch.setattr(fork.subprocess, "Popen", forbidden)
    evidence = tmp_path / "evidence"
    with pytest.raises(RuntimeError, match="OFFLINE_REVIEW_ONLY"):
        fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert not evidence.exists()
    assert attempted == []


def test_default_deny_occurs_before_any_original_cli_body():
    folder = Path(__file__).resolve().parents[1] / "postgres"
    tree = ast.parse(
        (folder / "run_postgres_security_patch_candidate.py").read_text(
            encoding="utf-8"))
    command = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "command")
    assert isinstance(command.body[0], ast.Raise)
    assert isinstance(command.body[0].exc, ast.Call)
    assert isinstance(command.body[0].exc.func, ast.Name)
    assert command.body[0].exc.func.id == "RuntimeError"
