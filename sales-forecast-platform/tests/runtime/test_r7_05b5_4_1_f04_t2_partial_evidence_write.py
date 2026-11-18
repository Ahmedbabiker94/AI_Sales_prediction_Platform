"""F04-T2: incomplete evidence must never produce accepted PASS manifest.

All Docker-like calls, image construction, signal transitions, and credential
values are synthetic/mocked. No real subprocess, Docker, PG, or OS signal.
The protected accepted runner is neither imported nor modified.
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
    restored = []
    monkeypatch.setattr(module, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old",
        signal=lambda _sig, handler: restored.append(handler)))
    return module, restored


def setup_success_path(module, monkeypatch, *, fail_preflight=False):
    calls = []
    records = {}
    if fail_preflight:
        def inspect(_):
            records["record"] = module._LIFECYCLE.get()
            raise RuntimeError("synthetic PRIMARY preflight failure")
        monkeypatch.setattr(module, "inspect_image", inspect)
    else:
        monkeypatch.setattr(module, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(module, "build_client",
                        lambda wheelhouse, evidence: "sha256:synthetic")
    monkeypatch.setattr(module, "remove_owned", lambda *_: True)

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "real subprocess invocation forbidden"
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
    return records, calls


def assert_rejected_evidence(module, restored, record, evidence):
    assert record["result"] == "FAILED"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert not (evidence / "run-manifest.json").exists()
    assert not (evidence / "run-manifest.json").is_symlink()
    assert len(restored) == 2
    assert restored[-1] == "old"
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("fault", ["partial_then_raise", "complete_then_raise", "before_write"])
def test_manifest_writer_error_cannot_publish_pass(
        fork, monkeypatch, tmp_path, fault):
    module, restored = fork
    _records, calls = setup_success_path(module, monkeypatch)
    captured = {}
    original_write = module.write_evidence
    intercepted = []

    def capture(**kwargs):
        captured["record"] = kwargs["record"]
        captured["envfile"] = kwargs["envfile"]
        assert captured["envfile"].exists()

    def faulty_write(path, text):
        if path.name == "run-manifest.json" or path.name.startswith(".run-manifest-"):
            intercepted.append(path)
            if fault == "partial_then_raise":
                # A deliberately valid-looking *partial* PASS prefix exercises
                # the danger of publishing directly to run-manifest.json.
                path.write_text('{"result":"PASS"}', encoding="utf-8")
            elif fault == "complete_then_raise":
                original_write(path, text)
            raise OSError("synthetic manifest " + fault)
        return original_write(path, text)

    monkeypatch.setattr(module, "write_evidence", faulty_write)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(OSError, match="synthetic manifest"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=capture)
        assert intercepted, "the terminal manifest writer must be exercised"
        assert calls, "the full body must have executed via mocks"
        assert not captured["envfile"].exists()
        assert_rejected_evidence(module, restored, captured["record"], evidence)
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_serialization_fault_classified_as_evidence_unavailable(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    _, calls = setup_success_path(module, monkeypatch)
    captured = {}
    def capture(**kwargs):
        captured["record"] = kwargs["record"]

    def fail_serializer(record, secrets):
        raise ValueError("synthetic terminal JSON serialization error")

    monkeypatch.setattr(module, "finalize_manifest_candidate", fail_serializer)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(ValueError, match="synthetic terminal JSON"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=capture)
        assert calls
        assert_rejected_evidence(module, restored, captured["record"], evidence)
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_publication_fault_cannot_leave_pass_manifest(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    _, calls = setup_success_path(module, monkeypatch)
    captured = {}

    def capture(**kwargs):
        captured["record"] = kwargs["record"]

    def deny_link(_source, _dest, **kwargs):
        raise OSError("synthetic atomic publish failed")

    monkeypatch.setattr(module.os, "link", deny_link)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(OSError, match="synthetic atomic publish failed"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=capture)
        assert calls
        assert_rejected_evidence(module, restored, captured["record"], evidence)
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_partial_stage_stdout_is_classified_and_never_accepted(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    _, calls = setup_success_path(module, monkeypatch)
    observed = {}
    original_write = module.write_evidence

    def capture(**kwargs):
        observed["record"] = kwargs["record"]

    def partial_log(path, text):
        if path.name == "foundation.stdout":
            observed["record"] = module._LIFECYCLE.get()
            path.write_text("synthetic partial test output", encoding="utf-8")
            raise OSError("synthetic stage stdout partial write")
        return original_write(path, text)

    monkeypatch.setattr(module, "write_evidence", partial_log)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(OSError, match="synthetic stage stdout partial"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=capture)
        assert calls, "main-body Docker calls must have been intercepted"
        assert "record" in observed
        assert observed["record"]["result"] == "FAILED"
        assert observed["record"]["evidence_status"] == "EVIDENCE_UNAVAILABLE"
        assert (evidence / "foundation.stdout").read_text() == "synthetic partial test output"
        manifest = evidence / "run-manifest.json"
        assert not manifest.exists() or json.loads(manifest.read_text())["result"] == "FAILED"
        assert len(restored) == 2 and restored[-1] == "old"
        assert module._LIFECYCLE.get() is None
        assert module._TEST_SECRETS.get() == ()
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_primary_error_not_masked_by_manifest_write_failure(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    records, _ = setup_success_path(module, monkeypatch, fail_preflight=True)
    original_write = module.write_evidence

    def faulty_write(path, text):
        if path.name == "run-manifest.json" or path.name.startswith(".run-manifest-"):
            path.write_text('{"result":"PASS"}', encoding="utf-8")
            raise OSError("synthetic secondary manifest write failure")
        return original_write(path, text)

    monkeypatch.setattr(module, "write_evidence", faulty_write)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(RuntimeError, match="synthetic PRIMARY preflight failure"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert_rejected_evidence(module, restored, records["record"], evidence)
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_manifest_publication_refuses_existing_destination_without_overwrite(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    _, calls = setup_success_path(module, monkeypatch)
    captured = {}
    existing = '{"existing_synthetic_artifact":true}'

    def occupy_destination(**kwargs):
        captured["record"] = kwargs["record"]
        (tmp_path / "evidence" / "run-manifest.json").write_text(
            existing, encoding="utf-8")

    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(FileExistsError):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=occupy_destination)
        assert calls
        assert (evidence / "run-manifest.json").read_text() == existing
        assert captured["record"]["result"] == "FAILED"
        assert captured["record"]["evidence_status"] == "EVIDENCE_UNAVAILABLE"
        assert len(restored) == 2 and restored[-1] == "old"
        assert module._LIFECYCLE.get() is None
        assert module._TEST_SECRETS.get() == ()
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())


def test_link_created_then_fault_removes_only_owned_published_manifest(
        fork, monkeypatch, tmp_path):
    module, restored = fork
    _, calls = setup_success_path(module, monkeypatch)
    captured = {}
    genuine_link = module.os.link
    linked = []

    def capture(**kwargs):
        captured["record"] = kwargs["record"]

    def ambiguous_publish(source, destination, **kwargs):
        genuine_link(source, destination, **kwargs)
        linked.append(Path(destination))
        raise OSError("synthetic link succeeded then callback raised")

    monkeypatch.setattr(module.os, "link", ambiguous_publish)
    evidence = tmp_path / "evidence"
    try:
        with pytest.raises(OSError, match="synthetic link succeeded"):
            module.run(allow=True, wheelhouse=tmp_path, evidence=evidence,
                       _host_stage=capture)
        assert linked == [evidence / "run-manifest.json"]
        assert calls
        assert_rejected_evidence(module, restored, captured["record"], evidence)
    finally:
        module._LIFECYCLE.set(None)
        module._TEST_SECRETS.set(())
