"""F04-T1: unlink results must be exact strings, never attacker-defined equality.

Candidate-only mock verification. No real subprocess, Docker/PG, OS signal,
network, or production credentials. The owned fixture file uses synthetic bytes.
The frozen accepted runner is neither imported nor changed.
"""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


class ForgedStatus:
    def __init__(self, target):
        self.target = target
        self.comparisons = 0

    def __eq__(self, other):
        self.comparisons += 1
        return other == self.target


class ComparisonExplodes:
    def __init__(self):
        self.comparisons = 0

    def __eq__(self, other):
        self.comparisons += 1
        raise RuntimeError("FORBIDDEN status equality invoked")


class SubclassedStatus(str):
    pass


def invalid_status(kind):
    if kind == "forged_removed":
        return ForgedStatus("REMOVED")
    if kind == "forged_not_present":
        return ForgedStatus("NOT_PRESENT")
    if kind == "raising_equality":
        return ComparisonExplodes()
    if kind == "str_subclass":
        return SubclassedStatus("REMOVED")
    if kind == "bytes":
        return b"REMOVED"
    if kind == "integer":
        return 1
    if kind == "none":
        return None
    if kind == "unknown":
        return "UNKNOWN"
    raise AssertionError(kind)


INVALID = [
    "forged_removed", "forged_not_present", "raising_equality",
    "str_subclass", "bytes", "integer", "none", "unknown",
]


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(module, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    restored = []
    monkeypatch.setattr(module, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old",
        signal=lambda _sig, handler: restored.append(handler)))
    return module, restored


def install_success_path(module, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(module, "build_client",
                        lambda wheelhouse, evidence: "sha256:synthetic")

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "real commands prohibited"
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
        if argv[1] == "ps" or argv[1:3] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="synthetic tests passed", stderr="")

    monkeypatch.setattr(module, "command", fake_command)
    monkeypatch.setattr(module, "remove_owned", lambda *_: True)
    return calls


@pytest.mark.parametrize("kind", INVALID + ["valid_removed", "valid_not_present"])
def test_full_success_path_requires_exact_plain_str_for_unlink(
        fork, monkeypatch, tmp_path, kind):
    module, restored = fork
    commands = install_success_path(module, monkeypatch)
    status = ("REMOVED" if kind == "valid_removed" else
              "NOT_PRESENT" if kind == "valid_not_present" else invalid_status(kind))
    captured = {}
    attempts = []
    real_unlink = module.remove_transient_credential_file

    def stage(**kwargs):
        captured["record"] = kwargs["record"]
        captured["envfile"] = kwargs["envfile"]
        assert kwargs["envfile"].exists(), "fake full-body path must create test envfile"

    def spoofed_unlink(path):
        attempts.append(path)
        assert real_unlink(path) == "REMOVED", "delete only owned fixture file"
        return status

    monkeypatch.setattr(module, "remove_transient_credential_file", spoofed_unlink)
    evidence = tmp_path / "evidence"
    try:
        if kind.startswith("valid_"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=stage)
        else:
            with pytest.raises(module.CleanupUnconfirmed, match="CLEANUP_UNCONFIRMED"):
                module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                           _host_stage=stage)

        record = captured["record"]
        expected_status = status if kind.startswith("valid_") else "UNCONFIRMED"
        assert type(record["credential_file_cleanup"]) is str
        assert record["credential_file_cleanup"] == expected_status
        assert record["result"] == ("PASS" if kind.startswith("valid_") else "FAILED")
        assert record["cleanup_passed"] is kind.startswith("valid_")
        assert record["cleanup_status"] == (
            "CLEANUP_SUCCESS" if kind.startswith("valid_") else "CLEANUP_UNCONFIRMED")
        manifest = json.loads((evidence / "run-manifest.json").read_text())
        assert manifest["result"] == record["result"]
        assert manifest["credential_file_cleanup"] == expected_status
        assert attempts == [captured["envfile"]]
        assert not captured["envfile"].exists()
        assert len(restored) == 2
        assert restored[-1] == "old"
        if hasattr(status, "comparisons"):
            assert status.comparisons == 0, "status equality methods must never run"
        assert commands, "fake complete success path was not exercised"
        assert module._LIFECYCLE.get() is None
        assert module._TEST_SECRETS.get() == ()
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


@pytest.mark.parametrize("kind", INVALID)
def test_interrupted_reconciliation_fallback_preserves_primary_and_rejects_objects(
        fork, monkeypatch, tmp_path, kind):
    module, restored = fork
    status = invalid_status(kind)
    captured = {}
    calls = []
    real_unlink = module.remove_transient_credential_file
    evidence = tmp_path / "evidence"

    def preflight(_):
        record = module._LIFECYCLE.get()
        captured["record"] = record
        captured["envfile"] = evidence / "test-client.env"
        captured["envfile"].write_text("synthetic-only", encoding="utf-8")
        record["creation_attempts"].append({
            "kind": "container",
            "name": "r7-05b3-" + record["run_hex"] + "-synthetic",
            "state": "DAEMON_UNRESOLVED",
        })
        raise RuntimeError("synthetic preflight")

    def stop_reconciliation(_):
        raise KeyboardInterrupt("synthetic reconciliation interrupted")

    def spoofed_unlink(path):
        calls.append(path)
        assert real_unlink(path) == "REMOVED"
        return status

    monkeypatch.setattr(module, "inspect_image", preflight)
    monkeypatch.setattr(module, "remove_owned", lambda *_: True)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=stop_reconciliation))
    monkeypatch.setattr(module, "remove_transient_credential_file", spoofed_unlink)

    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic reconciliation interrupted"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert calls == [captured["envfile"]]
        assert not captured["envfile"].exists()
        assert captured["record"]["result"] == "FAILED"
        assert captured["record"]["credential_file_cleanup"] == "UNCONFIRMED"
        assert captured["record"]["cleanup_passed"] is False
        assert captured["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert not (evidence / "run-manifest.json").exists()
        assert len(restored) == 2
        assert restored[-1] == "old"
        if hasattr(status, "comparisons"):
            assert status.comparisons == 0, "status equality methods must never run"
        assert module._LIFECYCLE.get() is None
        assert module._TEST_SECRETS.get() == ()
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())
