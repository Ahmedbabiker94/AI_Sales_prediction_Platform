"""F04-R2-04: a manifest alone is never proof of successful completion.

Candidate-only, fully mocked end-to-end evidence publication. Fault models
create the terminal hard link then raise while rollback or inode verification
fails. Direct bare-file readers see an apparent PASS; a fail-closed consumer
must also require an independently witnessed successful run return.

No real subprocesses, Docker, PostgreSQL, OS signals, or real credentials.
The protected accepted runner is not imported or changed.
"""
import importlib
import json
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
    signals = {"calls": [], "active": "old"}

    def install(_signum, handler):
        signals["calls"].append(handler)
        signals["active"] = handler

    monkeypatch.setattr(module, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old", signal=install))
    monkeypatch.setattr(module, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(module, "build_client", lambda *args: "sha256:synthetic")
    monkeypatch.setattr(module, "remove_owned", lambda *args: True)
    calls = []

    def command(argv, **kwargs):
        assert argv[0] == "docker", "real CLI invocation forbidden"
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
            return SimpleNamespace(returncode=0, stdout="synthetic-cluster", stderr="")
        if argv[1] == "ps" or argv[1:3] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="synthetic passed", stderr="")

    monkeypatch.setattr(module, "command", command)
    return module, signals, calls


def accepted_by_contract(module, path, *, run_completed):
    """Old bare-file consumer is an unsafe fallback: makes the test RED first."""
    def bare_file_consumer(path, *, run_completed):
        return json.loads(path.read_text(encoding="utf-8"))["result"] == "PASS"
    validator = getattr(module, "terminal_manifest_accepted_candidate",
                        bare_file_consumer)
    return validator(path, run_completed=run_completed)


@pytest.mark.parametrize("fault", ["rollback_unlink_error", "identity_check_error"])
def test_link_created_then_fault_never_accepts_bare_pass_without_run_success(
        fork, monkeypatch, tmp_path, fault):
    module, signals, calls = fork
    real_link = module.os.link
    real_unlink = Path.unlink
    real_samefile = module.os.path.samefile
    evidence = tmp_path / "evidence"
    manifest_path = evidence / "run-manifest.json"
    captured = {}
    linked = []

    def stage(**kwargs):
        captured["record"] = kwargs["record"]

    def link_then_raise(source, destination, **kwargs):
        real_link(source, destination, **kwargs)
        linked.append(Path(destination))
        raise OSError("synthetic post-link publication failure")

    def fail_target_unlink(path, *args, **kwargs):
        if Path(path) == manifest_path:
            raise OSError("synthetic terminal rollback refused")
        return real_unlink(path, *args, **kwargs)

    def fail_identity(source, destination):
        if Path(destination) == manifest_path:
            raise OSError("synthetic inode identity unavailable")
        return real_samefile(source, destination)

    monkeypatch.setattr(module.os, "link", link_then_raise)
    if fault == "rollback_unlink_error":
        monkeypatch.setattr(Path, "unlink", fail_target_unlink)
    else:
        monkeypatch.setattr(module.os.path, "samefile", fail_identity)

    # The published PASS may survive despite the runner's reported exception.
    with pytest.raises(OSError, match="synthetic post-link"):
        module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                   _host_stage=stage)

    assert linked == [manifest_path]
    assert calls
    assert manifest_path.is_file(), "combined fault leaves a published file"
    raw = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert manifest["result"] == "PASS", "this is the risky bare-file state"
    assert manifest["evidence_status"] == "AVAILABLE"
    assert captured["record"]["result"] == "FAILED"
    assert captured["record"]["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert accepted_by_contract(module, manifest_path, run_completed=False) is False, (
        "a file-only PASS must not be accepted after run() raises")
    assert signals["active"] == "old"
    assert len(signals["calls"]) == 2
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()


def test_confirmed_normal_run_and_unconfirmed_consumer_policy(
        fork, tmp_path):
    module, signals, calls = fork
    evidence = tmp_path / "evidence"
    result = module.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    # A regular return, not a file's mere existence, is the external witness.
    assert result is None
    path = evidence / "run-manifest.json"
    assert path.is_file()
    assert calls and len(signals["calls"]) == 2
    assert accepted_by_contract(module, path, run_completed=True) is True
    assert accepted_by_contract(module, path, run_completed=False) is False


@pytest.mark.parametrize("tamper", ["bad_flag_type", "redaction_count", "failed_cleanup", "missing_file"])
def test_consumer_rejects_untrustworthy_success_claim(
        fork, tmp_path, tamper):
    module, _signals, _calls = fork
    evidence = tmp_path / "evidence"
    module.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
    path = evidence / "run-manifest.json"
    if tamper == "bad_flag_type":
        assert accepted_by_contract(module, path, run_completed=1) is False
        return
    if tamper == "missing_file":
        path.unlink()
        assert accepted_by_contract(module, path, run_completed=True) is False
        return
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "redaction_count":
        parsed["secret_redactions"] = 1
    else:
        parsed["cleanup_passed"] = False
    path.write_text(json.dumps(parsed), encoding="utf-8")
    assert accepted_by_contract(module, path, run_completed=True) is False
