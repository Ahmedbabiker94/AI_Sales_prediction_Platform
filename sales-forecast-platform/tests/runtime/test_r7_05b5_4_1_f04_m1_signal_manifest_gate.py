"""F04-M1: no accepted manifest may predate successful SIGTERM restoration.

All subprocess/Docker/PG commands are intercepted by fake_command. Full-body
success is emulated with synthetic stdout and local, test-only evidence files.
The frozen accepted runner is never imported or modified.
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
    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    return runner


def install_fake_signal(fork, monkeypatch, fail_restore):
    seen = {"calls": [], "active": "prior"}

    def set_handler(signum, handler):
        assert signum == 15
        seen["calls"].append(handler)
        if fail_restore and handler == "prior":
            raise RuntimeError("synthetic SIGTERM restoration failed")
        seen["active"] = handler

    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "prior", signal=set_handler))
    return seen


def install_successful_fake_docker(fork, monkeypatch):
    calls = []
    monkeypatch.setattr(fork, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(fork, "build_client", lambda wheelhouse, evidence: "sha256:synthetic")
    monkeypatch.setattr(fork, "remove_owned",
                        lambda kind, name, owner: calls.append(("remove", kind, name)) or True)

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "no actual subprocess or Git command allowed"
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
        if argv[1:] == ["ps", "-aq", "--filter", argv[-1]]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1:3] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="synthetic tests passed", stderr="")

    monkeypatch.setattr(fork, "command", fake_command)
    return calls


@pytest.mark.parametrize("fail_restore", [False, True])
def test_successful_mocked_main_body_cannot_publish_pass_before_signal_restore(
        fork, monkeypatch, tmp_path, fail_restore):
    signals = install_fake_signal(fork, monkeypatch, fail_restore)
    calls = install_successful_fake_docker(fork, monkeypatch)
    record = {}
    saved_writer = fork.write_evidence
    saved_link = fork.os.link
    published_manifest = []

    def record_before_manifest_write(path, content):
        if path.name.startswith(".run-manifest-"):
            assert signals["active"] == "prior", "manifest staged before SIGTERM restoration"
        return saved_writer(path, content)

    def record_actual_manifest_publication(source, destination, **kwargs):
        if Path(destination).name == "run-manifest.json":
            assert signals["active"] == "prior", "manifest published before SIGTERM restoration"
            published_manifest.append(Path(destination))
        return saved_link(source, destination, **kwargs)

    monkeypatch.setattr(fork, "write_evidence", record_before_manifest_write)
    monkeypatch.setattr(fork.os, "link", record_actual_manifest_publication)

    def capture_stage(**kwargs):
        record["value"] = kwargs["record"]

    evidence = tmp_path / "evidence"
    try:
        if fail_restore:
            with pytest.raises(fork.CleanupUnconfirmed,
                               match="SIGTERM handler restoration not confirmed"):
                fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                         _host_stage=capture_stage)
            assert not published_manifest
            assert not (evidence / "run-manifest.json").exists()
            assert record["value"]["result"] == "FAILED"
            assert record["value"]["cleanup_passed"] is False
            assert record["value"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
            assert record["value"]["handler_restoration"] == "UNCONFIRMED"
        else:
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                     _host_stage=capture_stage)
            assert len(published_manifest) == 1
            saved = json.loads((evidence / "run-manifest.json").read_text())
            assert saved["result"] == "PASS"
            assert saved["cleanup_passed"] is True
            assert saved["cleanup_status"] == "CLEANUP_SUCCESS"
            assert saved["handler_restoration"] == "RESTORED"
        assert len(signals["calls"]) == 2, "restoration is a single attempt"
        assert calls, "main success path must be fully mocked, not skipped"
        assert not (evidence / "test-client.env").exists()
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())


def test_simultaneous_primary_and_restoration_errors_keep_primary_failure(
        fork, monkeypatch, tmp_path):
    signals = install_fake_signal(fork, monkeypatch, fail_restore=True)
    captured = {}

    def synthetic_preflight(_):
        captured["record"] = fork._LIFECYCLE.get()
        raise RuntimeError("synthetic PRIMARY preflight failure")

    monkeypatch.setattr(fork, "inspect_image", synthetic_preflight)
    monkeypatch.setattr(fork, "command",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(RuntimeError, match="synthetic PRIMARY preflight failure"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert len(signals["calls"]) == 2
        assert captured["record"]["result"] == "FAILED"
        assert captured["record"]["handler_restoration"] == "UNCONFIRMED"
        saved = json.loads((evidence / "run-manifest.json").read_text())
        assert saved["result"] == "FAILED"
        assert saved["cleanup_passed"] is False
        assert saved["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert saved["handler_restoration"] == "UNCONFIRMED"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
    finally:
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
