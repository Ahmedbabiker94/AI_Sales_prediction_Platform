"""Offline B5-4-1 Git provenance negative tests; no Docker or PostgreSQL."""
import importlib
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres")


@pytest.fixture
def good_git(monkeypatch, runner, tmp_path):
    root = tmp_path / "worktree" / "sales-forecast-platform"
    root.mkdir(parents=True)
    monkeypatch.setattr(runner, "ROOT", root)
    calls = []
    def fake_command(args, **kwargs):
        calls.append(args)
        assert args[:3] == ["git", "-C", str(root)]
        if args[3:] == ["rev-parse", "--show-toplevel"]:
            value = str(root.parent) + "\n"
        elif args[3:] == ["rev-parse", "--verify", "HEAD"]:
            value = "a" * 40 + "\n"
        elif args[3:] == ["status", "--porcelain", "--untracked-files=all"]:
            value = ""
        else:
            raise AssertionError("unexpected command")
        return subprocess.CompletedProcess(args, 0, value, "")
    monkeypatch.setattr(runner, "command", fake_command)
    return root, calls


def test_clean_nested_git_worktree_passes(runner, good_git):
    _, calls = good_git
    assert runner.verified_clean_source() == "a" * 40
    assert len(calls) == 3
    assert calls[-1][-2:] == ["--porcelain", "--untracked-files=all"]


@pytest.mark.parametrize("dirty", [
    " M sales-forecast-platform/src/changed.py\n",
    "?? sales-forecast-platform/new.txt\n",
    " M outside-project.txt\n",
])
def test_dirty_or_untracked_worktree_fails_closed(runner, good_git, monkeypatch, dirty):
    _, calls = good_git
    original = runner.command
    def changed(args, **kwargs):
        if args[-3:] == ["status", "--porcelain", "--untracked-files=all"]:
            return subprocess.CompletedProcess(args, 0, dirty, "")
        return original(args, **kwargs)
    monkeypatch.setattr(runner, "command", changed)
    with pytest.raises(ValueError, match="checkout must be clean"):
        runner.verified_clean_source()


def test_wrong_checkout_root_rejected(runner, good_git, monkeypatch):
    original = runner.command
    def wrong(args, **kwargs):
        if args[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(args, 0, "/unexpected/repo\n", "")
        return original(args, **kwargs)
    monkeypatch.setattr(runner, "command", wrong)
    with pytest.raises(ValueError, match="expected Git worktree"):
        runner.verified_clean_source()


def test_invalid_commit_rejected(runner, good_git, monkeypatch):
    original = runner.command
    def invalid(args, **kwargs):
        if args[-3:] == ["rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "not-a-sha\n", "")
        return original(args, **kwargs)
    monkeypatch.setattr(runner, "command", invalid)
    with pytest.raises(ValueError, match="invalid source commit"):
        runner.verified_clean_source()


def test_unreadable_windows_git_pointer_fails_closed(runner, good_git, monkeypatch):
    original = runner.command
    def unavailable(args, **kwargs):
        if args[-2:] == ["rev-parse", "--show-toplevel"]:
            raise RuntimeError("fatal: not a git repository")
        return original(args, **kwargs)
    monkeypatch.setattr(runner, "command", unavailable)
    with pytest.raises(RuntimeError, match="not a git repository"):
        runner.verified_clean_source()


def test_failed_provenance_never_creates_evidence_or_uses_docker(
        runner, good_git, monkeypatch, tmp_path):
    def reject():
        raise ValueError("dirty checkout")
    monkeypatch.setattr(runner, "verified_clean_source", reject)
    evidence = tmp_path / "evidence"
    with pytest.raises(ValueError, match="dirty checkout"):
        runner.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert not evidence.exists()


def test_explicit_opt_in_still_required(runner, good_git, tmp_path):
    with pytest.raises(ValueError, match="explicit disposable-database authorization"):
        runner.run(allow=False, wheelhouse=tmp_path, evidence=tmp_path / "evidence")
