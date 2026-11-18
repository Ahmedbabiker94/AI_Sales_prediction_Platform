"""F04-R2-04 R3: candidate controller owns completion, run ID and digest.

Entirely synthetic: fake Docker/PG CLI, signals and resource callbacks.
The on-disk candidate's command() remains OFFLINE_REVIEW_ONLY, frozen runner
remains untouched, and no real Docker/PG/OS signals are executed.
"""
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    mod = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(mod, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(mod, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    signals = {"calls": [], "current": "old"}
    def install(signum, callback):
        assert signum == 15
        signals["calls"].append(callback)
        signals["current"] = callback
    monkeypatch.setattr(mod, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old", signal=install))
    monkeypatch.setattr(mod, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(mod, "build_client", lambda *_: "sha256:synthetic")
    monkeypatch.setattr(mod, "remove_owned", lambda *_: True)
    calls = []
    def fake_command(argv, **kwargs):
        assert argv[0] == "docker", "real subprocess forbidden"
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
            return SimpleNamespace(returncode=0, stdout="same-cluster", stderr="")
        if argv[1] == "ps" or argv[1:3] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="synthetic PASS", stderr="")
    monkeypatch.setattr(mod, "command", fake_command)
    return mod, signals, calls


@pytest.fixture
def admission(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_manifest_admission_controller_candidate")


def test_synchronous_controller_binds_same_invocation_digest_and_bytes(
        admission, runner, tmp_path):
    module, signals, calls = runner
    snapshot = admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a")
    assert snapshot is not None
    assert type(snapshot.manifest_bytes) is bytes
    assert hashlib.sha256(snapshot.manifest_bytes).hexdigest() == snapshot.sha256
    raw = (tmp_path / "run-a" / "run-manifest.json").read_bytes()
    assert raw == snapshot.manifest_bytes
    manifest = json.loads(raw)
    assert snapshot.run_id == manifest["run_id"]
    assert manifest["run_hex"] == snapshot.run_id.replace("-", "")
    assert manifest["result"] == "PASS"
    assert manifest["cleanup_passed"] is True
    assert calls and signals["current"] == "old"
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()
    assert "OFFLINE_REVIEW_ONLY" in Path(module.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", ["rollback_refused", "identity_unavailable"])
def test_controller_does_not_issue_receipt_after_ambiguous_failed_publication(
        admission, runner, monkeypatch, tmp_path, fault):
    module, signals, calls = runner
    evidence = tmp_path / "run-b"
    accepted = evidence / "run-manifest.json"
    genuine_link = module.os.link
    genuine_unlink = Path.unlink
    genuine_samefile = module.os.path.samefile
    def link_then_raise(staging, destination, **kwargs):
        genuine_link(staging, destination, **kwargs)
        raise OSError("synthetic published then failed")
    def refuse_unlink(p, *args, **kwargs):
        if Path(p) == accepted:
            raise OSError("synthetic rollback denied")
        return genuine_unlink(p, *args, **kwargs)
    def refuse_inode(source, destination):
        if Path(destination) == accepted:
            raise OSError("synthetic identity verification denied")
        return genuine_samefile(source, destination)
    monkeypatch.setattr(module.os, "link", link_then_raise)
    if fault == "rollback_refused":
        monkeypatch.setattr(Path, "unlink", refuse_unlink)
    else:
        monkeypatch.setattr(module.os.path, "samefile", refuse_inode)
    with pytest.raises(OSError, match="synthetic published then failed"):
        admission.invoke_candidate_and_admit(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert accepted.exists()
    assert json.loads(accepted.read_text())["result"] == "PASS"
    assert admission.admit_archive_without_attestation_candidate(accepted) is None
    assert calls and signals["current"] == "old"
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()


def test_cross_run_substitution_cannot_borrow_success_of_another_run(
        admission, runner, monkeypatch, tmp_path):
    _module, _signals, calls = runner
    # B is an unrelated successful run; a copied B file cannot borrow A's exit.
    b = admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-b")
    assert b is not None
    observed = []
    genuine_read = admission._read_manifest_bytes
    def replace_a_with_b(path):
        if Path(path) == tmp_path / "run-a" / "run-manifest.json":
            observed.append("substitute")
            return b.manifest_bytes
        return genuine_read(path)
    monkeypatch.setattr(admission, "_read_manifest_bytes", replace_a_with_b)
    a = admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a")
    assert a is None
    assert observed == ["substitute"]
    assert calls
    assert admission.admit_archive_without_attestation_candidate(
        tmp_path / "run-a" / "run-manifest.json") is None


def test_same_run_artifact_content_substitution_rejected_by_digest(
        admission, runner, monkeypatch, tmp_path):
    _module, _signals, _calls = runner
    genuine_read = admission._read_manifest_bytes
    def change_bytes(path):
        raw = genuine_read(path)
        if raw is not None and Path(path).name == "run-manifest.json":
            return raw + b" "
        return raw
    monkeypatch.setattr(admission, "_read_manifest_bytes", change_bytes)
    assert admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a") is None


def test_same_run_id_but_changed_valid_json_metadata_rejected(
        admission, runner, monkeypatch, tmp_path):
    _module, _signals, _calls = runner
    genuine_read = admission._read_manifest_bytes
    def alter_metadata(path):
        raw = genuine_read(path)
        if raw is None:
            return None
        changed = json.loads(raw)
        changed["fake_extra_metadata"] = "tampered"
        return json.dumps(changed, indent=2, sort_keys=True).encode("utf-8")
    monkeypatch.setattr(admission, "_read_manifest_bytes", alter_metadata)
    assert admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a") is None


def test_host_stage_mutated_invocation_id_never_admitted(
        admission, runner, tmp_path):
    _module, _signals, _calls = runner
    def bad_stage(**kwargs):
        kwargs["record"]["run_id"] = "00000000-0000-0000-0000-000000000000"
    assert admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a",
        _host_stage=bad_stage) is None


@pytest.mark.parametrize("error", [
    RuntimeError("synthetic preflight failure"),
    TimeoutError("synthetic timeout"),
    KeyboardInterrupt("synthetic interrupt"),
    SystemExit("synthetic termination"),
])
def test_failed_or_unknown_invocation_is_not_admitted(
        admission, runner, monkeypatch, tmp_path, error):
    module, _signals, _calls = runner
    def stop(_):
        raise error
    monkeypatch.setattr(module, "inspect_image", stop)
    with pytest.raises(type(error), match="synthetic"):
        admission.invoke_candidate_and_admit(
            allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-failed")
    assert admission.admit_archive_without_attestation_candidate(
        tmp_path / "run-failed" / "run-manifest.json") is None
    assert module._LIFECYCLE.get() is None
    assert module._TEST_SECRETS.get() == ()


def test_archived_or_restarted_file_only_consumer_is_fail_closed(
        admission, runner, tmp_path):
    _module, _signals, _calls = runner
    receipt = admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "run-a")
    assert receipt is not None
    from_archive = tmp_path / "copy" / "run-manifest.json"
    from_archive.parent.mkdir()
    from_archive.write_bytes(receipt.manifest_bytes)
    assert admission.admit_archive_without_attestation_candidate(from_archive) is None
    assert admission.admit_archive_without_attestation_candidate(
        tmp_path / "run-a" / "run-manifest.json") is None
    # The trusted controller exposes a copied immutable validated snapshot,
    # not a promise that an arbitrarily reopened file remains unchanged.
    assert hashlib.sha256(receipt.manifest_bytes).hexdigest() == receipt.sha256


def test_candidate_b5_4_1_reporting_consumer_only_admits_bound_success(
        admission, runner, monkeypatch, tmp_path):
    _module, signals, calls = runner
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    wrapper = importlib.import_module("run_b5_4_1_postgres_admission_candidate")
    evidence = tmp_path / "run-reporting"
    invoked = []
    def reporting_stage(**kwargs):
        invoked.append(kwargs["run_id"])
        kwargs["record"]["b5_4_1_evidence_dir"] = str(evidence)
    receipt = wrapper.run_reporting_stage_with_bound_admission_candidate(
        allow=True, wheelhouse=tmp_path, evidence=evidence,
        reporting_stage=reporting_stage)
    assert receipt is not None
    assert len(invoked) == 1
    assert str(invoked[0]) == receipt.run_id
    assert json.loads(receipt.manifest_bytes)["b5_4_1_evidence_dir"] == str(evidence)
    assert hashlib.sha256(receipt.manifest_bytes).hexdigest() == receipt.sha256
    assert signals["current"] == "old" and calls
    assert wrapper.__file__.endswith("run_b5_4_1_postgres_admission_candidate.py")
    code = Path(wrapper.__file__).read_text(encoding="utf-8")
    assert "from run_postgres import" not in code
    assert "if __name__" not in code


def test_candidate_reporting_consumer_failure_does_not_admit_file(
        admission, runner, monkeypatch, tmp_path):
    _module, signals, calls = runner
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    wrapper = importlib.import_module("run_b5_4_1_postgres_admission_candidate")
    evidence = tmp_path / "failed-reporting"
    def fail_stage(**_kwargs):
        raise RuntimeError("synthetic reporting stage failure")
    with pytest.raises(RuntimeError, match="synthetic reporting stage failure"):
        wrapper.run_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence,
            reporting_stage=fail_stage)
    terminal = evidence / "run-manifest.json"
    assert not terminal.exists() or json.loads(terminal.read_text())["result"] == "FAILED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert signals["current"] == "old" and calls


def test_normal_return_without_observed_run_identity_must_reject(
        admission, monkeypatch, tmp_path):
    # A successful return alone is not sufficient; _host_stage must bind the
    # same runner invocation's exact UUID and finalized lifecycle record.
    monkeypatch.setattr(admission, "_candidate_run", lambda **_kwargs: None)
    assert admission.invoke_candidate_and_admit(
        allow=True, wheelhouse=tmp_path, evidence=tmp_path / "absent") is None


def test_existing_operational_wrapper_is_still_frozen_source_and_not_candidate():
    operational = (Path(__file__).resolve().parents[1] / "postgres"
                   / "run_b5_4_1_postgres.py").read_text(encoding="utf-8")
    assert "from run_postgres import (" in operational
    assert "run_manifest_admission_controller_candidate" not in operational
    # This test is a reminder that candidate integration is NOT operational
    # wrapper rebaseline or production consumer coverage.
