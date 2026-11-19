"""F04-R2-03: a finalizer-rejected manifest and retained lifecycle must agree.

Mocks exercise a fully successful *synthetic* main body so the candidate's
terminal PASS promotion is reached before unexpected secret finalization.
No actual subprocess, Docker, PostgreSQL, signal handler or credentials are used.
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
    module = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(module, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    signals = {"calls": [], "active": "prior"}

    def signal_handler(signum, handler):
        assert signum == 15
        signals["calls"].append(handler)
        signals["active"] = handler

    monkeypatch.setattr(module, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "prior", signal=signal_handler))
    monkeypatch.setattr(module, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(module, "build_client",
                        lambda *_: "sha256:synthetic")
    monkeypatch.setattr(module, "remove_owned", lambda *_: True)
    calls = []

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "no real CLI, Git, Docker or PG"
        calls.append(list(argv))
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

    monkeypatch.setattr(module, "command", fake_command)
    return module, signals, calls


@pytest.mark.parametrize("case", ["nested_value", "nested_key", "prior_redactions"])
def test_finalizer_rejection_reclassifies_retained_record_and_removes_secrets(
        fork, tmp_path, case):
    module, signals, calls = fork
    captured = {}
    secret_values = []

    def stage(**kwargs):
        record = kwargs["record"]
        captured["record"] = record
        secret_values.extend(module._TEST_SECRETS.get())
        assert all(value.startswith("test-only-") for value in secret_values)
        if case == "nested_value":
            record["unexpected_metadata"] = {
                "details": [{"value": "marker-" + secret_values[0]}]}
        elif case == "nested_key":
            record["unexpected_metadata"] = {
                "details": {secret_values[1]: "synthetic-key-exposure"}}
        else:
            record["secret_redactions"] = 2

    evidence = tmp_path / "evidence"
    with pytest.raises(module.CleanupUnconfirmed, match="CLEANUP_UNCONFIRMED"):
        module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                   _host_stage=stage)

    assert calls, "the fully mocked successful body must execute"
    manifest_path = evidence / "run-manifest.json"
    assert manifest_path.is_file()
    raw = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    record = captured["record"]
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_passed"] is False
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert manifest["secret_redactions"] >= (2 if case == "prior_redactions" else 1)
    assert record["result"] == "FAILED", "R2-03: retained record cannot remain PASS"
    assert record["cleanup_passed"] is False
    assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert record["secret_redactions"] == manifest["secret_redactions"]
    # The exact same finalized, redacted classification must be observable
    # via the retained original dict reference captured by _host_stage.
    assert record == manifest, "retained and published terminal states diverged"
    for secret in secret_values:
        assert secret not in raw
        assert secret not in repr(record), "retained record must not retain secrets"
    if case == "nested_key":
        assert "synthetic-key-exposure" in raw
        assert "<redacted-disposable-secret>" in raw
    if case == "nested_value":
        assert "<redacted-disposable-secret>" in raw
    assert signals["calls"] and len(signals["calls"]) == 2
    assert signals["active"] == "prior"
    assert not (evidence / "test-client.env").exists()
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()


def test_no_rejection_still_publishes_matching_pass_record(fork, tmp_path):
    module, signals, calls = fork
    captured = {}

    def stage(**kwargs):
        captured["record"] = kwargs["record"]

    evidence = tmp_path / "evidence"
    module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
               _host_stage=stage)
    manifest = json.loads((evidence / "run-manifest.json").read_text())
    assert calls
    assert manifest["result"] == "PASS"
    assert manifest["cleanup_passed"] is True
    assert manifest["cleanup_status"] == "CLEANUP_SUCCESS"
    assert captured["record"] == manifest
    assert signals["active"] == "prior"
    assert len(signals["calls"]) == 2
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()
