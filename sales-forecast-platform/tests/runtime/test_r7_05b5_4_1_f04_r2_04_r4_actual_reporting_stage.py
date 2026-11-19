"""F-04 R2-04 R4: actual B5-4-1 stage through SAME candidate lifecycle.

The test fixture intercepts *every* runner Docker command and all OS signals;
no real Docker/PostgreSQL/subprocess is used. Candidate hard-stop remains.
The actual operational wrapper is read only for a source-parity check.
"""
import ast
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PGDIR = Path(__file__).resolve().parents[1] / "postgres"
TESTS = (
    "tests/postgres/test_stored_forecast_accuracy_postgres.py",
    "tests/postgres/test_canonical_reporting_read.py",
    "tests/postgres/test_r7_05b5_4_1_host_reporting_requests.py",
)


@pytest.fixture
def system(monkeypatch):
    monkeypatch.syspath_prepend(str(PGDIR))
    runner = importlib.import_module("run_postgres_security_patch_candidate")
    admission = importlib.import_module("run_manifest_admission_controller_candidate")
    consumer = importlib.import_module("run_b5_4_1_postgres_admission_candidate")
    stage = importlib.import_module("run_b5_4_1_reporting_stage_candidate")
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=lambda: 10.0, sleep=lambda _: None))
    signal_calls = []
    monkeypatch.setattr(runner, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old",
        signal=lambda signum, callback: signal_calls.append(callback)))
    monkeypatch.setattr(runner, "inspect_image", lambda _: {"Id": "synthetic"})
    monkeypatch.setattr(runner, "build_client", lambda *_: "sha256:synthetic")
    observed = {"commands": [], "removals": [], "stage_seen": [], "fault": None}
    def remove(kind, name, owner):
        observed["removals"].append((kind, name, str(owner)))
        return True
    monkeypatch.setattr(runner, "remove_owned", remove)

    def command(args, **kwargs):
        assert args[0] == "docker", "unexpected real CLI"
        observed["commands"].append(tuple(args))
        if args[1:3] == ["volume", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such volume")
        if args[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps([{
                "Config": {"Env": []}
            }]), stderr="")
        if args[1] == "exec" and "/proc/1/comm" in args:
            return SimpleNamespace(returncode=0, stdout="postgres\n", stderr="")
        if args[1] == "exec":
            return SimpleNamespace(returncode=0, stdout="synthetic-cluster", stderr="")
        if args[1] == "ps" or args[1:3] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if (args[1] == "run" and "--name" in args
                and args[args.index("--name") + 1].endswith("-b5-4-1")):
            # This is the REAL reporting-stage argv, not a stub callback.
            lifecycle = runner._LIFECYCLE.get()
            assert lifecycle is not None
            stage_name = args[args.index("--name") + 1]
            assert stage_name in lifecycle.get("extra_containers", [])
            observed["stage_seen"].append((stage_name, lifecycle))
            if observed["fault"] == "interrupt":
                raise KeyboardInterrupt("synthetic reporting stage interruption")
            if observed["fault"] == "failure":
                return SimpleNamespace(returncode=9, stdout="synthetic failure",
                                       stderr="synthetic stage error")
            return SimpleNamespace(returncode=0, stdout="synthetic reporting ok",
                                   stderr="synthetic reporting stderr")
        return SimpleNamespace(returncode=0, stdout="synthetic pass", stderr="")
    monkeypatch.setattr(runner, "command", command)
    # Captured imported aliases on the candidate adapter are tied to the
    # same runtime module; patch both aliases to the same mock reference.
    monkeypatch.setattr(stage, "command", command)
    return runner, admission, consumer, stage, signal_calls, observed


def test_real_reporting_stage_candidate_source_parity(system):
    _runner, _admission, _consumer, stage, _signals, _observed = system
    operational = ast.parse((PGDIR / "run_b5_4_1_postgres.py").read_text())
    candidate = ast.parse(Path(stage.__file__).read_text())
    original_def = next(x for x in operational.body
                        if isinstance(x, ast.FunctionDef)
                        and x.name == "reporting_stage")
    candidate_def = next(x for x in candidate.body
                         if isinstance(x, ast.FunctionDef)
                         and x.name == "reporting_stage")
    # Only TWO exact deltas are permitted relative to the frozen wrapper:
    # (1) _LIFECYCLE import points to the same candidate runtime;
    # (2) both B5 stdout/stderr writes have one fail-closed catch that marks
    # incomplete evidence FAILED/EVIDENCE_UNAVAILABLE and re-raises unchanged.
    from copy import deepcopy
    flattened = deepcopy(candidate_def)
    wrapped = [(i, node) for i, node in enumerate(flattened.body)
               if isinstance(node, ast.Try)]
    assert len(wrapped) == 1, "unexpected candidate try/except changes"
    i, guard = wrapped[0]
    assert len(guard.body) == 2
    assert len(guard.handlers) == 1
    assert not guard.orelse and not guard.finalbody
    handler = guard.handlers[0]
    assert isinstance(handler.type, ast.Name)
    assert handler.type.id == "BaseException"
    assert handler.name is None
    expected_handler = ast.parse(
        "record['evidence_status'] = 'EVIDENCE_UNAVAILABLE'\n"
        "record['result'] = 'FAILED'\n"
        "raise\n").body
    assert ast.dump(ast.Module(body=handler.body, type_ignores=[]),
                    include_attributes=False) == ast.dump(
        ast.Module(body=expected_handler, type_ignores=[]),
        include_attributes=False)
    flattened.body[i:i+1] = guard.body
    assert len(original_def.body) == len(flattened.body)
    assert ast.dump(original_def, include_attributes=False).replace(
        "module='run_postgres'", "module='run_postgres_security_patch_candidate'"
    ) == ast.dump(flattened, include_attributes=False)
    source = Path(stage.__file__).read_text()
    assert "from run_postgres import" not in source
    assert "if __name__" not in source
    assert stage.TESTS == TESTS


def test_actual_reporting_stage_success_uses_one_owner_lifecycle(system, tmp_path):
    runner, admission, consumer, stage, signals, observed = system
    evidence = tmp_path / "reporting"
    receipt = consumer.run_actual_reporting_stage_with_bound_admission_candidate(
        allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert receipt is not None
    manifest = json.loads(receipt.manifest_bytes)
    assert receipt.run_id == manifest["run_id"]
    assert hashlib.sha256(receipt.manifest_bytes).hexdigest() == receipt.sha256
    operations = [x for x in manifest["operations"] if x["stage"] == "b5-4-1"]
    assert len(operations) == 1
    assert operations[0]["tests"] == list(TESTS)
    assert operations[0]["exit_code"] == 0
    name, lifecycle = observed["stage_seen"][0]
    assert lifecycle["run_id"] == receipt.run_id
    assert name.startswith("r7-05b3-" + receipt.run_id.replace("-", ""))
    assert any(kind == "container" and x == name for kind, x, _ in observed["removals"])
    assert (evidence / "b5-4-1.stdout").read_text() == "synthetic reporting ok"
    assert (evidence / "b5-4-1.stderr").read_text() == "synthetic reporting stderr"
    stage_cmd = next(x for x in observed["commands"]
                     if "--name" in x and x[x.index("--name") + 1].endswith("-b5-4-1"))
    assert stage_cmd[-len(TESTS):] == TESTS
    assert "--network=none" in stage_cmd
    assert "--read-only" in stage_cmd
    assert "--cap-drop=ALL" in stage_cmd
    assert "--security-opt=no-new-privileges" in stage_cmd
    assert "--env-file" in stage_cmd
    assert signals[-1] == "old" and len(signals) == 2
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None


@pytest.mark.parametrize("fault,exception", [
    ("failure", RuntimeError),
    ("interrupt", KeyboardInterrupt),
])
def test_actual_reporting_failure_or_interrupt_reconciles_owned_container(
        system, tmp_path, fault, exception):
    runner, admission, consumer, _stage, signals, observed = system
    observed["fault"] = fault
    evidence = tmp_path / "report-fault"
    with pytest.raises(exception, match="synthetic reporting|isolated B5"):
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert len(observed["stage_seen"]) == 1
    name, lifecycle = observed["stage_seen"][0]
    assert any(kind == "container" and x == name for kind, x, _ in observed["removals"])
    assert lifecycle["result"] == "FAILED"
    terminal = evidence / "run-manifest.json"
    assert not terminal.exists() or json.loads(terminal.read_text())["result"] == "FAILED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_reporting_partial_stage_evidence_fails_closed(
        system, monkeypatch, tmp_path, stream):
    runner, admission, consumer, stage, signals, observed = system
    original_write = stage.write_evidence
    evidence = tmp_path / ("partial-" + stream)
    def fail_reporting_output(path, contents):
        if path.name == "b5-4-1." + stream:
            path.write_text("partial synthetic evidence", encoding="utf-8")
            raise OSError("synthetic reporting " + stream + " I/O failure")
        return original_write(path, contents)
    monkeypatch.setattr(stage, "write_evidence", fail_reporting_output)
    with pytest.raises(OSError, match="synthetic reporting " + stream + " I/O failure"):
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert (evidence / ("b5-4-1." + stream)).read_text() == "partial synthetic evidence"
    assert len(observed["stage_seen"]) == 1
    name, lifecycle = observed["stage_seen"][0]
    assert any(kind == "container" and x == name for kind, x, _ in observed["removals"])
    assert name in lifecycle["possible_owned_containers"]
    assert lifecycle["result"] == "FAILED"
    assert lifecycle["evidence_status"] == "EVIDENCE_UNAVAILABLE", (
        "partial B5 evidence must not be classified AVAILABLE in retained state")
    assert lifecycle["credential_file_cleanup"] == "REMOVED"
    terminal = evidence / "run-manifest.json"
    assert terminal.is_file(), "a fail-closed terminal report is expected here"
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["result"] == "FAILED"
    assert manifest["evidence_status"] == "EVIDENCE_UNAVAILABLE", (
        "published FAILED manifest cannot claim complete B5 evidence")
    assert manifest["run_id"] == lifecycle["run_id"]
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


def test_reporting_stage_rejects_foreign_lifecycle_before_any_docker(
        system, tmp_path):
    runner, _admission, _consumer, stage, _signals, observed = system
    from uuid import uuid4
    own_id = uuid4()
    token = runner._LIFECYCLE.set({"run_hex": "wrong-run"})
    try:
        with pytest.raises(RuntimeError, match="original disposable run lifecycle required"):
            stage.reporting_stage(
                server="synthetic", socket="synthetic", client="sha256:synthetic",
                envfile=tmp_path/"test-client.env", run_id=own_id,
                database="testdb", record={"b5_4_1_evidence_dir": str(tmp_path),
                                           "operations": []})
        assert observed["commands"] == []
    finally:
        runner._LIFECYCLE.reset(token)


def test_reporting_stdout_stderr_test_secrets_are_redacted_and_rejected(
        system, monkeypatch, tmp_path, capsys):
    """A real B5-style mocked stdout/stderr must not leak test passwords."""
    runner, admission, consumer, stage, signals, observed = system
    synthetic_secrets = []
    original_command = stage.command
    def stdout_stderr_with_disposable_secrets(args, **kwargs):
        response = original_command(args, **kwargs)
        if (args[1] == "run" and "--name" in args
                and args[args.index("--name") + 1].endswith("-b5-4-1")):
            current = runner._TEST_SECRETS.get()
            assert len(current) >= 2
            assert all(secret.startswith("test-only-") for secret in current)
            synthetic_secrets.extend(current[:2])
            return SimpleNamespace(
                returncode=0,
                stdout="synthetic stdout " + current[0],
                stderr="synthetic stderr " + current[1])
        return response
    monkeypatch.setattr(stage, "command", stdout_stderr_with_disposable_secrets)
    evidence = tmp_path / "synthetic-test-passwords"
    with pytest.raises(runner.CleanupUnconfirmed, match="CLEANUP_UNCONFIRMED"):
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert len(synthetic_secrets) == 2
    output = capsys.readouterr()
    terminal = evidence / "run-manifest.json"
    assert terminal.is_file()
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert manifest["secret_redactions"] >= 2
    for item in (evidence / "b5-4-1.stdout", evidence / "b5-4-1.stderr"):
        assert item.is_file()
        assert "<redacted-disposable-secret>" in item.read_text(encoding="utf-8")
    for secret in synthetic_secrets:
        assert secret not in output.out
        assert secret not in output.err
        assert secret not in terminal.read_text(encoding="utf-8")
        assert secret not in (evidence / "b5-4-1.stdout").read_text(encoding="utf-8")
        assert secret not in (evidence / "b5-4-1.stderr").read_text(encoding="utf-8")
    assert len(observed["stage_seen"]) == 1
    name, lifecycle = observed["stage_seen"][0]
    assert lifecycle["result"] == "FAILED"
    assert any(kind == "container" and x == name for kind, x, _ in observed["removals"])
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


# F04 / P1 (planning-only): reporting-stage uncertain daemon and multi-fault
# source-transfer negatives. The current on-disk candidate's command() is
# NEVER permitted to execute: every command and signal remains a fake.

@pytest.mark.parametrize("scenario", ["late_resource", "inventory_unavailable"])
def test_p1_reporting_timeout_uncertain_daemon_cannot_be_admitted(
        system, monkeypatch, tmp_path, scenario):
    """Model an already-sanitized command timeout plus DAEMON_UNRESOLVED.

    The subprocess helper's own internal timeout behavior is independently
    tested by the H1/R2-01 AST-only tests. This case exercises propagation
    through the REAL B5 stage, controller and bounded reconciliation.
    """
    runner, admission, consumer, stage, signals, observed = system
    original_command = stage.command
    original_remove = runner.remove_owned
    captured = {"attempt": None, "record": None, "rounds": 0}
    moment = {"value": 0.0}
    def tick():
        moment["value"] += 0.8
        return moment["value"]
    monkeypatch.setattr(runner.time, "monotonic", tick)
    expected_error = RuntimeError(
        "disposable PostgreSQL command timed out; command details suppressed")

    def with_uncertain_mutation(args, **kwargs):
        if (args[1] == "run" and "--name" in args
                and args[args.index("--name") + 1].endswith("-b5-4-1")):
            record = runner._LIFECYCLE.get()
            assert record is not None
            name = args[args.index("--name") + 1]
            assert name in record.get("extra_containers", [])
            # Safe mock of command()'s pre-spawn creation ledger and uncertain
            # daemon state; no actual timeout, Popen, Docker, PG or signals.
            attempt = {"kind": "container", "name": name,
                       "started_ns": 1, "state": "DAEMON_UNRESOLVED",
                       "local_reaped": True}
            record["creation_attempts"].append(attempt)
            captured.update(attempt=attempt, record=record)
            observed["stage_seen"].append((name, record))
            raise expected_error
        if (scenario == "inventory_unavailable" and
                (args[1:3] == ["ps", "-aq"]
                 or args[1:3] == ["volume", "ls"])):
            raise OSError("synthetic read-only inventory unavailable")
        return original_command(args, **kwargs)

    def late_or_missing(kind, name, owner):
        observed["removals"].append((kind, name, str(owner)))
        if kind == "container" and name.endswith("-b5-4-1"):
            captured["rounds"] += 1
            # A late daemon-side appearance cannot turn ambiguous creation
            # into authoritative cleanup even after a later removal succeeds.
            return captured["rounds"] > 1 if scenario == "late_resource" else False
        return original_remove(kind, name, owner)
    monkeypatch.setattr(stage, "command", with_uncertain_mutation)
    monkeypatch.setattr(runner, "command", with_uncertain_mutation)
    monkeypatch.setattr(runner, "remove_owned", late_or_missing)
    evidence = tmp_path / ("p1-" + scenario)
    # Explicit precedence: unknown daemon completion makes cleanup
    # unconfirmed, and the public gate MUST report CleanupUnconfirmed rather
    # than an ordinary test timeout. The retained error_type still records
    # the original sanitized RuntimeError without raw command details.
    with pytest.raises(runner.CleanupUnconfirmed,
                       match="owned resource or credential cleanup not confirmed") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert caught.value is not expected_error
    assert captured["attempt"]["state"] == "DAEMON_UNRESOLVED"
    record = captured["record"]
    assert record["result"] == "FAILED"
    assert record["error_type"] == "RuntimeError"
    assert record["cleanup_passed"] is False
    assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert captured["attempt"]["name"] in record["possible_owned_containers"]
    assert captured["rounds"] > 0
    if scenario == "late_resource":
        assert captured["rounds"] > 1, "test must simulate late appearance"
    else:
        assert record["remaining_containers"] == "UNAVAILABLE"
        assert record["remaining_volumes"] == "UNAVAILABLE"
    terminal = evidence / "run-manifest.json"
    assert terminal.is_file()
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_passed"] is False
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_p1_reporting_output_control_flow_interrupt_preserves_identity(
        system, monkeypatch, tmp_path, stream, error_type):
    runner, admission, consumer, stage, signals, observed = system
    original_write = stage.write_evidence
    evidence = tmp_path / ("p1-" + stream + "-" + error_type.__name__)
    initial = error_type("synthetic exact write interruption object")
    def interrupted_write(path, contents):
        if path.name == "b5-4-1." + stream:
            path.write_text("partial test-only output", encoding="utf-8")
            raise initial
        return original_write(path, contents)
    monkeypatch.setattr(stage, "write_evidence", interrupted_write)
    with pytest.raises(error_type) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert caught.value is initial, "must not reconstruct/mask original exception"
    assert (evidence / ("b5-4-1." + stream)).read_text() == "partial test-only output"
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert name in record["possible_owned_containers"]
    assert record["result"] == "FAILED"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert record["credential_file_cleanup"] == "REMOVED"
    assert any(kind == "container" and item == name
               for kind, item, _ in observed["removals"])
    terminal = evidence / "run-manifest.json"
    assert terminal.is_file()
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["result"] == "FAILED"
    assert manifest["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("secondary", ["cleanup_refusal", "publication_failure"])
def test_p1_partial_write_secondary_fault_respects_cleanup_precedence(
        system, monkeypatch, tmp_path, secondary):
    runner, admission, consumer, stage, signals, observed = system
    original_write = stage.write_evidence
    original_remove = runner.remove_owned
    evidence = tmp_path / ("p1-combined-" + secondary)
    primary = OSError("synthetic ORIGINAL partial B5 output write")
    attempted = {"secondary": False}
    def partial_write(path, contents):
        if path.name == "b5-4-1.stderr":
            path.write_text("incomplete stdout-stderr pair", encoding="utf-8")
            raise primary
        return original_write(path, contents)
    def refuse_reporting_cleanup(kind, name, owner):
        if kind == "container" and name.endswith("-b5-4-1"):
            attempted["secondary"] = True
            observed["removals"].append((kind, name, str(owner)))
            return False
        return original_remove(kind, name, owner)
    def publication_failure(path, data):
        attempted["secondary"] = True
        raise OSError("synthetic SECONDARY manifest publication fault")
    monkeypatch.setattr(stage, "write_evidence", partial_write)
    if secondary == "cleanup_refusal":
        monkeypatch.setattr(runner, "remove_owned", refuse_reporting_cleanup)
    else:
        monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                            publication_failure)
    # With uncertain owned cleanup, fail-closed CleanupUnconfirmed has
    # precedence over the original OSError. With publication failure and
    # otherwise confirmed cleanup, the runner preserves the original OSError
    # object despite the secondary publication error.
    public_type = (runner.CleanupUnconfirmed
                   if secondary == "cleanup_refusal" else OSError)
    with pytest.raises(public_type) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    if secondary == "cleanup_refusal":
        assert "CLEANUP_UNCONFIRMED" in str(caught.value)
    else:
        assert caught.value is primary, "publication fault must not mask primary"
    assert attempted["secondary"], "fault injection must actually execute"
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert record["result"] == "FAILED"
    assert record["error_type"] == "OSError"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert any(kind == "container" and item == name
               for kind, item, _ in observed["removals"])
    terminal = evidence / "run-manifest.json"
    if secondary == "publication_failure":
        assert not terminal.exists()
    else:
        assert terminal.is_file()
        manifest = json.loads(terminal.read_text(encoding="utf-8"))
        assert manifest["result"] == "FAILED"
        assert manifest["evidence_status"] == "EVIDENCE_UNAVAILABLE"
        assert manifest["cleanup_passed"] is False
        assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


def test_p1_a_02_three_way_write_cleanup_and_publication_failure(
        system, monkeypatch, tmp_path):
    """Source-derived counterexample: no manifest, cleanup cannot be proved.

    Previous code re-raised the primary stage OSError after publication failure
    even though the container removal was refused. Public classification must
    be CleanupUnconfirmed instead. All fake operations remain in-process.
    """
    runner, admission, consumer, stage, signals, observed = system
    evidence = tmp_path / "p1-three-way"
    primary = OSError("synthetic primary B5 partial stderr write")
    publisher_error = OSError("synthetic secondary manifest staging failure")
    actual_write = stage.write_evidence
    actual_remove = runner.remove_owned
    seen = {"container_refused": [], "publication_failed": False}

    def partial_write(path, contents):
        if path.name == "b5-4-1.stderr":
            path.write_text("synthetic partial", encoding="utf-8")
            raise primary
        return actual_write(path, contents)

    def removal_refused(kind, name, owner):
        if kind == "container" and name.endswith("-b5-4-1"):
            seen["container_refused"].append(name)
            observed["removals"].append((kind, name, str(owner)))
            return False
        return actual_remove(kind, name, owner)

    def publication_failure(path, manifest_text):
        seen["publication_failed"] = True
        assert path == evidence / "run-manifest.json"
        raise publisher_error

    monkeypatch.setattr(stage, "write_evidence", partial_write)
    monkeypatch.setattr(runner, "remove_owned", removal_refused)
    monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                        publication_failure)

    with pytest.raises(runner.CleanupUnconfirmed, match="CLEANUP_UNCONFIRMED") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)

    assert caught.value is not primary
    assert seen["container_refused"] and seen["publication_failed"]
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert name in seen["container_refused"]
    assert record["result"] == "FAILED"
    assert record["cleanup_passed"] is False
    assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert record["error_type"] == "OSError"
    assert record["credential_file_cleanup"] == "REMOVED"
    assert not (evidence / "run-manifest.json").exists()
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("fail_publication", [False, True])
def test_p1_a_01_real_b5_unicode_decode_no_secret_in_public_exception_chain(
        system, monkeypatch, tmp_path, fail_publication):
    """Use real in-memory AST command behind on-disk OFFLINE_REVIEW_ONLY.

    Inject a decoding error from a synthetic Popen communicate() with secret
    bytes, through actual B5 reporting, uncertain Docker mutation/cleanup,
    and optionally publication failure. Neither the final public error nor any
    linked primary/secondary exception may reveal the credential.
    """
    import traceback
    from test_r7_05b5_4_1_f04_h1_timeout_sanitization import (
        offline_command, SECRET)
    runner, admission, consumer, stage, signals, observed = system
    command, seen = offline_command(runner, "decode")
    fake_stage_command = stage.command
    evidence = tmp_path / ("p1-decoding-" + str(fail_publication))
    captured = {}
    tick_value = {"t": 0.0}

    def tick():
        tick_value["t"] += 0.7
        return tick_value["t"]

    def inject_decode(args, **kwargs):
        if (args[1] == "run" and "--name" in args
                and args[args.index("--name") + 1].endswith("-b5-4-1")):
            record = runner._LIFECYCLE.get()
            assert record is not None
            captured["record"] = record
            captured["name"] = args[args.index("--name") + 1]
            return command(args, timeout=3)
        return fake_stage_command(args, **kwargs)

    def reject_publish(path, manifest_text):
        assert path == evidence / "run-manifest.json"
        raise OSError("synthetic publication unavailable")

    monkeypatch.setattr(stage, "command", inject_decode)
    monkeypatch.setattr(runner.time, "monotonic", tick)
    if fail_publication:
        monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                            reject_publish)
    with pytest.raises(runner.CleanupUnconfirmed,
                       match="CLEANUP_UNCONFIRMED") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    queue, visited = [caught.value], set()
    while queue:
        exc = queue.pop()
        if exc is None or id(exc) in visited:
            continue
        visited.add(id(exc))
        values = [
            str(exc), repr(exc), repr(exc.args), repr(vars(exc)),
            repr(getattr(exc, "object", None)),
            repr(getattr(exc, "cmd", None)),
            repr(getattr(exc, "output", None)),
            repr(getattr(exc, "stderr", None)),
            "".join(traceback.format_exception(exc)),
        ]
        assert all(SECRET not in value and "PGPASSWORD" not in value
                   for value in values)
        queue.extend((exc.__context__, exc.__cause__))
    assert captured["name"] in captured["record"]["possible_owned_containers"]
    assert captured["record"]["error_type"] == "RuntimeError"
    assert captured["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert captured["record"]["result"] == "FAILED"
    assert captured["record"]["credential_file_cleanup"] == "REMOVED"
    assert seen["processes"]
    assert captured["record"]["creation_attempts"][-1]["state"] == "DAEMON_UNRESOLVED"
    terminal = evidence / "run-manifest.json"
    if fail_publication:
        assert not terminal.exists()
    else:
        assert terminal.is_file()
        raw = terminal.read_text(encoding="utf-8")
        assert SECRET not in raw
        assert json.loads(raw)["result"] == "FAILED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("inventory", ["containers", "volumes"])
def test_p1_inventory_failure_alone_prevents_acceptance(
        system, monkeypatch, tmp_path, inventory):
    """No uncertain mutation, no denied cleanup; only a listing failure."""
    runner, admission, consumer, stage, signals, observed = system
    evidence = tmp_path / ("p1-isolated-inventory-" + inventory)
    real_command = runner.command
    seen = []

    def inventory_only_failure(args, **kwargs):
        if (inventory == "containers" and args[1] == "ps"
                and "-aq" in args):
            seen.append("containers")
            raise OSError("synthetic read-only container inventory unavailable")
        if (inventory == "volumes" and args[1:3] == ["volume", "ls"]):
            seen.append("volumes")
            raise OSError("synthetic read-only volume inventory unavailable")
        return real_command(args, **kwargs)

    monkeypatch.setattr(runner, "command", inventory_only_failure)
    with pytest.raises(runner.CleanupUnconfirmed,
                       match="CLEANUP_UNCONFIRMED"):
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert seen == [inventory]
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert not record["creation_attempts"], (
        "mocked stage has no unresolved creation ledger; inventory alone blocks")
    assert name in record["possible_owned_containers"]
    assert record["cleanup_passed"] is False
    assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert record["result"] == "FAILED"
    assert record["remaining_" + inventory] == "UNAVAILABLE"
    assert record["remaining_" + ("volumes" if inventory == "containers"
                                  else "containers")] == ""
    assert any(kind == "container" and obj == name
               for kind, obj, _ in observed["removals"])
    terminal = evidence / "run-manifest.json"
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


def test_p1_absence_then_late_labeled_container_reappearance_is_rejected(
        system, monkeypatch, tmp_path):
    """Model returned 'absent' on cleanup then late observation in inventory.

    This demonstrates controller/runner fail-closed ordering on mock events;
    it is NOT proof of a real Docker daemon's event timing or quiescence.
    """
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / "p1-absent-then-late"
    original_remove = runner.remove_owned
    original_command = runner.command
    stages = []

    def observed_absent(kind, name, owner):
        if kind == "container" and name.endswith("-b5-4-1"):
            stages.append(("cleanup-confirmed-absent", name))
            observed["removals"].append((kind, name, str(owner)))
            return True
        return original_remove(kind, name, owner)

    def late_inventory(args, **kwargs):
        if args[1] == "ps" and "-aq" in args:
            assert stages, "late resource must be observed AFTER prior absence"
            stages.append(("late-owned-container-found", stages[0][1]))
            return SimpleNamespace(returncode=0,
                                   stdout=stages[0][1] + "\n", stderr="")
        return original_command(args, **kwargs)

    monkeypatch.setattr(runner, "remove_owned", observed_absent)
    monkeypatch.setattr(runner, "command", late_inventory)
    with pytest.raises(runner.CleanupUnconfirmed,
                       match="CLEANUP_UNCONFIRMED"):
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert [key for key, _ in stages] == [
        "cleanup-confirmed-absent", "late-owned-container-found"]
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert name == stages[0][1]
    assert record["creation_attempts"] == [], (
        "without unresolved creation, later observed resource alone blocks")
    assert record["remaining_containers"] == name
    assert record["remaining_volumes"] == ""
    assert record["cleanup_passed"] is False
    assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert record["result"] == "FAILED"
    terminal = evidence / "run-manifest.json"
    manifest = json.loads(terminal.read_text(encoding="utf-8"))
    assert manifest["remaining_containers"] == name
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert manifest["result"] == "FAILED"
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


# F04-P1-C: publisher exceptions are untrusted external-data surfaces.
# A bare "from None" is NOT enough: __context__ may still reference them.
# No real subprocess, Docker, signal, PG or user credential is involved.
P1C_SECRET = "test-only-P1C-PUBLISHER-CREDENTIAL-NOT-REAL"


def _assert_p1c_public_exception_has_no_publisher_payload(public):
    import traceback
    queue, seen = [public], set()
    while queue:
        exc = queue.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        displayed = (
            str(exc), repr(exc), repr(exc.args), repr(vars(exc)),
            repr(getattr(exc, "object", None)),
            repr(getattr(exc, "filename", None)),
            repr(getattr(exc, "stderr", None)),
            repr(getattr(exc, "output", None)),
            repr(getattr(exc, "cmd", None)),
            "".join(traceback.format_exception(exc)),
        )
        assert all(P1C_SECRET not in item for item in displayed), (
            "unsafe publisher exception retained in public exception graph")
        queue.extend((exc.__context__, exc.__cause__))


@pytest.mark.parametrize("publisher_type", ["unicode", "oserror"])
@pytest.mark.parametrize("prior", ["owned_cleanup_unconfirmed",
                                     "safe_primary", "publisher_only"])
def test_p1_c_01_unsafe_publisher_exception_never_leaks_at_any_exit(
        system, monkeypatch, tmp_path, publisher_type, prior):
    """Six negative paths: unsafe publisher with three completion states.

    The controller invokes the actual B5-stage candidate under the already
    mocked command/daemon. The publishing callback is the ONLY unsafe source.
    """
    runner, admission, consumer, stage, signals, observed = system
    evidence = tmp_path / ("p1c-" + publisher_type + "-" + prior)
    original_write = stage.write_evidence
    original_remove = runner.remove_owned
    primary = OSError("synthetic SAFE PRIMARY B5 output failure")
    events = {"publication": 0, "refusal": 0}

    def stage_write(path, data):
        if prior != "publisher_only" and path.name == "b5-4-1.stderr":
            path.write_text("synthetic incomplete stage output", encoding="utf-8")
            raise primary
        return original_write(path, data)

    def refuse_owned(kind, name, owner):
        if (prior == "owned_cleanup_unconfirmed" and kind == "container"
                and name.endswith("-b5-4-1")):
            events["refusal"] += 1
            observed["removals"].append((kind, name, str(owner)))
            return False
        return original_remove(kind, name, owner)

    unsafe_bytes = ("synthetic publisher " + P1C_SECRET).encode() + b"\xff"
    if publisher_type == "unicode":
        unsafe = UnicodeDecodeError("utf-8", unsafe_bytes,
                                    len(unsafe_bytes) - 1, len(unsafe_bytes),
                                    "synthetic publisher decode")
    else:
        unsafe = OSError(5, "synthetic publisher " + P1C_SECRET,
                         "/synthetic/" + P1C_SECRET)
        unsafe.object = unsafe_bytes
        unsafe.stderr = P1C_SECRET

    def unsafe_publish(path, text):
        assert path == evidence / "run-manifest.json"
        events["publication"] += 1
        raise unsafe

    monkeypatch.setattr(stage, "write_evidence", stage_write)
    monkeypatch.setattr(runner, "remove_owned", refuse_owned)
    monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                        unsafe_publish)

    if prior == "owned_cleanup_unconfirmed":
        expected_type = runner.CleanupUnconfirmed
    elif prior == "safe_primary":
        expected_type = OSError
    else:
        expected_type = OSError  # fixed-message sanitized publication exception

    with pytest.raises(expected_type) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    public = caught.value
    assert public is not unsafe, "publisher error object itself is never public"
    if prior == "safe_primary":
        assert public is primary, "safe primary identity must be preserved"
    elif prior == "owned_cleanup_unconfirmed":
        assert "CLEANUP_UNCONFIRMED" in str(public)
        assert events["refusal"] > 0
    else:
        assert "publication failed" in str(public)
        assert "details suppressed" in str(public)
    _assert_p1c_public_exception_has_no_publisher_payload(public)
    assert events["publication"] == 1
    assert len(observed["stage_seen"]) == 1
    _name, record = observed["stage_seen"][0]
    assert record["result"] == "FAILED"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert record["credential_file_cleanup"] == "REMOVED"
    if prior == "owned_cleanup_unconfirmed":
        assert record["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    else:
        assert record["cleanup_passed"] is True
    assert not (evidence / "run-manifest.json").exists()
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_p1_c_01_publisher_interrupt_keeps_class_but_not_unsafe_data(
        system, monkeypatch, tmp_path, interrupt_type):
    """Publisher exit/interrupt is public but unsafe message and chain are not."""
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1c-publisher-" + interrupt_type.__name__)
    unsafe = interrupt_type("synthetic publisher " + P1C_SECRET)
    unsafe.object = (P1C_SECRET + "-synthetic").encode()
    recorded = []

    def interrupted_publish(path, manifest_text):
        assert path == evidence / "run-manifest.json"
        recorded.append(True)
        raise unsafe

    monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                        interrupted_publish)
    with pytest.raises(interrupt_type, match="details suppressed") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert caught.value is not unsafe
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    _assert_p1c_public_exception_has_no_publisher_payload(caught.value)
    assert recorded == [True]
    assert len(observed["stage_seen"]) == 1
    _name, record = observed["stage_seen"][0]
    assert record["result"] == "FAILED"
    assert record["evidence_status"] == "EVIDENCE_UNAVAILABLE"
    assert record["cleanup_passed"] is True
    assert record["credential_file_cleanup"] == "REMOVED"
    assert not (evidence / "run-manifest.json").exists()
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


# F04-P1-D HIGH: json.JSONDecodeError.doc retains raw Docker inspect stdout,
# including actual randomly created disposable test credentials. An exception
# must be safely reconstructed before it reaches runner cleanup/finalization.
@pytest.mark.parametrize("inspection", ["server", "client"])
@pytest.mark.parametrize("mode", [
    "confirmed",
    "confirmed_publisher_fault",
    "unconfirmed",
    "unconfirmed_publisher_fault",
])
def test_p1_d_unsafe_inspect_json_error_never_reaches_public_exception(
        system, monkeypatch, tmp_path, inspection, mode):
    import traceback
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1d-" + inspection + "-" + mode)
    original_command = runner.command
    original_remove = runner.remove_owned
    captured = {"record": None, "secret": None, "bad_json": None,
                "publisher_attempted": 0, "cleanup_refused": 0, "inspect_attempted": 0}
    publisher_fault = "publisher_fault" in mode
    cleanup_uncertain = mode.startswith("unconfirmed")

    def with_malformed_inspect(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"] and len(argv) == 3:
            name = argv[2]
            is_target = ((inspection == "server" and name.endswith("-server"))
                         or (inspection == "client" and name.endswith("-foundation")))
            if is_target:
                record = runner._LIFECYCLE.get()
                secrets = runner._TEST_SECRETS.get()
                assert record is not None and len(secrets) == 4
                secret = secrets[0]
                malformed = json.dumps([{
                    "Config": {"Env": ["PGPASSWORD=" + secret,
                                       "OTHER_TEST_VALUE=" + secret]},
                    "provenance": "synthetic Docker inspect metadata"}])[:-1]
                assert secret in malformed
                captured.update(record=record, secret=secret, bad_json=malformed)
                captured["inspect_attempted"] += 1
                return SimpleNamespace(returncode=0, stdout=malformed, stderr="")
        return original_command(argv, **kwargs)

    def mocked_remove(kind, name, owner):
        if (cleanup_uncertain and kind == "container"
                and name.endswith("-server")):
            captured["cleanup_refused"] += 1
            observed["removals"].append((kind, name, str(owner)))
            return False
        return original_remove(kind, name, owner)

    def untrusted_publisher(path, manifest_text):
        captured["publisher_attempted"] += 1
        assert path == evidence / "run-manifest.json"
        raise OSError("synthetic PUBLISHER " + captured["secret"])

    monkeypatch.setattr(runner, "command", with_malformed_inspect)
    monkeypatch.setattr(runner, "remove_owned", mocked_remove)
    if publisher_fault:
        monkeypatch.setattr(runner, "publish_terminal_manifest_candidate",
                            untrusted_publisher)

    exception_type = runner.CleanupUnconfirmed if cleanup_uncertain else RuntimeError
    with pytest.raises(exception_type) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)

    assert captured["inspect_attempted"] == 1
    assert captured["record"] is not None
    secret = captured["secret"]
    assert secret and secret.startswith("test-only-")
    assert secret in captured["bad_json"]
    assert caught.value.__class__ is exception_type
    if cleanup_uncertain:
        assert "CLEANUP_UNCONFIRMED" in str(caught.value)
        assert captured["cleanup_refused"] > 0
    else:
        assert "inspection JSON invalid" in str(caught.value)
        assert captured["cleanup_refused"] == 0

    # Check public attributes, not only the usual traceback rendering:
    # JSONDecodeError.doc and args can retain the entire metadata string.
    queue, visited = [caught.value], set()
    while queue:
        exc = queue.pop()
        if exc is None or id(exc) in visited:
            continue
        visited.add(id(exc))
        printed = (
            str(exc), repr(exc), repr(exc.args), repr(vars(exc)),
            repr(getattr(exc, "doc", None)),
            repr(getattr(exc, "object", None)),
            repr(getattr(exc, "obj", None)),
            repr(getattr(exc, "filename", None)),
            repr(getattr(exc, "stderr", None)),
            "".join(traceback.format_exception(exc)),
        )
        assert all(secret not in s for s in printed), (
            "Docker-inspect source credentials leak via exception attributes")
        queue.extend((exc.__context__, exc.__cause__))

    record = captured["record"]
    assert record["result"] == "FAILED"
    assert record["error_type"] == "RuntimeError"
    assert record["cleanup_passed"] is (not cleanup_uncertain)
    assert record["cleanup_status"] == (
        "CLEANUP_SUCCESS" if not cleanup_uncertain else "CLEANUP_UNCONFIRMED")
    assert record["credential_file_cleanup"] == ("NOT_PRESENT" if inspection == "server" else "REMOVED")
    assert not (evidence / "test-client.env").exists()
    assert observed["stage_seen"] == [], "invalid inspect must stop before reporting"
    assert captured["publisher_attempted"] == int(publisher_fault)
    terminal = evidence / "run-manifest.json"
    if publisher_fault:
        assert not terminal.exists()
    else:
        assert terminal.is_file()
        manifest = json.loads(terminal.read_text(encoding="utf-8"))
        assert manifest["result"] == "FAILED"
        assert manifest["cleanup_passed"] is (not cleanup_uncertain)
        assert manifest["error_type"] == "RuntimeError"
        assert secret not in terminal.read_text(encoding="utf-8")
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


# F04-P1-E: syntactically valid but unsupported Docker-inspect JSON shapes.
# The existing fixture's Env=[] is valid; the OUTER empty list is not.
P1E_SHAPES = (
    "env_string", "env_dict_item", "env_list_item", "env_int_item",
    "env_none_item", "env_null", "env_empty_string", "env_character_array",
    "env_no_equals", "outer_empty",
    "outer_null", "outer_object", "first_not_object", "config_missing",
    "config_null", "config_array", "env_missing", "outer_multiple",
)
P1E_MODES = tuple(
    (shape, mode)
    for shape in P1E_SHAPES
    for mode in (("confirmed", "unconfirmed",
                 "confirmed_publisher_fault", "unconfirmed_publisher_fault")
                 if shape in ("env_string", "env_dict_item")
                 else ("confirmed", "unconfirmed"))
)


def _p1e_inspect_shape(shape, secret):
    value = "PGPASSWORD=" + secret
    valid = {"Config": {"Env": [value]}}
    if shape == "env_string":
        return [{"Config": {"Env": value}}]
    if shape == "env_dict_item":
        return [{"Config": {"Env": [{"PASSWORD=": secret}]}}]
    if shape == "env_list_item":
        return [{"Config": {"Env": [[value]]}}]
    if shape == "env_int_item":
        return [{"Config": {"Env": [42, value]}}]
    if shape == "env_none_item":
        return [{"Config": {"Env": [None, value]}}]
    if shape == "env_null":
        return [{"Config": {"Env": None}, "raw_hint": secret}]
    if shape == "env_empty_string":
        return [{"Config": {"Env": ""}, "raw_hint": secret}]
    if shape == "env_character_array":
        return [{"Config": {"Env": list(value)}}]
    if shape == "env_no_equals":
        return [{"Config": {"Env": ["NOT_AN_ENV_ENTRY", value]}}]
    if shape == "outer_empty":
        return []
    if shape == "outer_null":
        return None
    if shape == "outer_object":
        return valid
    if shape == "first_not_object":
        return [value]
    if shape == "config_missing":
        return [{"Env": [value]}]
    if shape == "config_null":
        return [{"Config": None, "raw_hint": secret}]
    if shape == "config_array":
        return [{"Config": [value]}]
    if shape == "env_missing":
        return [{"Config": {"Other": value}}]
    if shape == "outer_multiple":
        return [valid, {"Config": {"Env": [value]}}]
    raise AssertionError("unsupported test shape")


@pytest.mark.parametrize("inspection", ["server", "client"])
@pytest.mark.parametrize("shape,mode", P1E_MODES)
def test_p1_e_valid_json_unsupported_inspect_metadata_never_admitted(
        system, monkeypatch, tmp_path, inspection, shape, mode):
    """Two actual parse sites through candidate runner/controller; no Docker.

    Env string previously fragmented a contiguous password; Env dictionary
    previously could raise AttributeError with raw dictionary in .obj.
    """
    import traceback
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1e-" + inspection + "-" + shape + "-" + mode)
    old_command, old_remove = runner.command, runner.remove_owned
    unconfirmed = mode.startswith("unconfirmed")
    pub_fail = mode.endswith("publisher_fault")
    seen = {"record": None, "secret": None, "raw": None,
            "inspects": 0, "publish": 0, "refused": 0}

    def inspect_command(args, **kwargs):
        if args[:2] == ["docker", "inspect"] and len(args) == 3:
            name = args[2]
            target = ((inspection == "server" and name.endswith("-server"))
                      or (inspection == "client" and name.endswith("-foundation")))
            if target:
                record = runner._LIFECYCLE.get()
                secrets = runner._TEST_SECRETS.get()
                assert record is not None and len(secrets) == 4
                secret = secrets[0]
                raw = json.dumps(_p1e_inspect_shape(shape, secret))
                seen.update(record=record, secret=secret, raw=raw)
                seen["inspects"] += 1
                return SimpleNamespace(returncode=0, stdout=raw, stderr="")
        return old_command(args, **kwargs)

    def remove(kind, name, owner):
        if unconfirmed and kind == "container" and name.endswith("-server"):
            seen["refused"] += 1
            observed["removals"].append((kind, name, str(owner)))
            return False
        return old_remove(kind, name, owner)

    def publish(path, contents):
        assert path == evidence / "run-manifest.json"
        seen["publish"] += 1
        raise OSError("synthetic publisher " + seen["secret"])

    monkeypatch.setattr(runner, "command", inspect_command)
    monkeypatch.setattr(runner, "remove_owned", remove)
    if pub_fail:
        monkeypatch.setattr(runner, "publish_terminal_manifest_candidate", publish)
    expected_type = runner.CleanupUnconfirmed if unconfirmed else RuntimeError
    with pytest.raises(expected_type) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)

    assert seen["inspects"] == 1
    record, secret = seen["record"], seen["secret"]
    assert record is not None and secret and secret.startswith("test-only-")
    if shape == "env_character_array":
        # The exploit splits a real credential into individual JSON strings;
        # only joining them reconstructs it. Exact-string checks miss it.
        assert secret not in seen["raw"]
        assert "".join(json.loads(seen["raw"])[0]["Config"]["Env"]) == (
            "PGPASSWORD=" + secret)
    elif shape not in ("outer_empty", "outer_null"):
        assert secret in seen["raw"]
    if unconfirmed:
        assert seen["refused"] > 0
        assert "CLEANUP_UNCONFIRMED" in str(caught.value)
    else:
        assert type(caught.value) is RuntimeError
        assert "inspection metadata schema invalid" in str(caught.value)

    queue, visited = [caught.value], set()
    while queue:
        exc = queue.pop()
        if exc is None or id(exc) in visited:
            continue
        visited.add(id(exc))
        visible = (str(exc), repr(exc), repr(exc.args), repr(vars(exc)),
                   repr(getattr(exc, "doc", None)),
                   repr(getattr(exc, "object", None)),
                   repr(getattr(exc, "obj", None)),
                   repr(getattr(exc, "filename", None)),
                   "".join(traceback.format_exception(exc)))
        assert all(secret not in part for part in visible)
        queue.extend((exc.__context__, exc.__cause__))

    assert record["result"] == "FAILED"
    assert record["error_type"] == "RuntimeError"
    assert record["cleanup_passed"] is (not unconfirmed)
    assert record["cleanup_status"] == (
        "CLEANUP_UNCONFIRMED" if unconfirmed else "CLEANUP_SUCCESS")
    assert record["credential_file_cleanup"] == (
        "NOT_PRESENT" if inspection == "server" else "REMOVED")
    assert not (evidence / "test-client.env").exists()
    assert observed["stage_seen"] == [], "bad metadata must stop before reporting"
    assert seen["publish"] == int(pub_fail)
    terminal = evidence / "run-manifest.json"
    if pub_fail:
        assert not terminal.exists()
    else:
        assert terminal.is_file()
        data = terminal.read_text(encoding="utf-8")
        assert secret not in data
        manifest = json.loads(data)
        assert manifest["result"] == "FAILED"
        assert manifest["cleanup_passed"] is (not unconfirmed)
        assert manifest["error_type"] == "RuntimeError"
        assert manifest["credential_file_cleanup"] == (
            "NOT_PRESENT" if inspection == "server" else "REMOVED")
    assert admission.admit_archive_without_attestation_candidate(terminal) is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("inspection", ["server", "client"])
def test_p1_e_valid_inspect_env_list_still_admits_redacted_snapshot(
        system, monkeypatch, tmp_path, inspection):
    """The strict schema check must not reject a legitimate KEY=VALUE list.

    The secret is generated in the actual runner, not a literal test fixture.
    Every value with a password-bearing key is redacted before the retained
    manifest is finalized; only the verified controller snapshot is consumed.
    """
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1e-valid-" + inspection)
    old_command = runner.command
    captured = {"secret": None, "count": 0}
    def valid_inspect(args, **kwargs):
        if args[:2] == ["docker", "inspect"] and len(args) == 3:
            name = args[2]
            match = ((inspection == "server" and name.endswith("-server"))
                     or (inspection == "client" and name.endswith("-foundation")))
            if match:
                secrets = runner._TEST_SECRETS.get()
                assert len(secrets) == 4
                captured["count"] += 1
                captured["secret"] = secrets[0]
                return SimpleNamespace(returncode=0, stdout=json.dumps([{
                    "Config": {"Env": [
                        "POSTGRES_PASSWORD=" + secrets[0],
                        "SAFE_FEATURE_FLAG=enabled",
                    ]}, "Synthetic": "only-for-offline-test",
                }]), stderr="")
        return old_command(args, **kwargs)
    monkeypatch.setattr(runner, "command", valid_inspect)
    receipt = consumer.run_actual_reporting_stage_with_bound_admission_candidate(
        allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert receipt is not None
    assert captured["count"] == 1
    assert captured["secret"].startswith("test-only-")
    raw = receipt.manifest_bytes.decode("utf-8")
    assert captured["secret"] not in raw
    manifest = json.loads(raw)
    assert manifest["result"] == "PASS"
    assert manifest["evidence_status"] == "AVAILABLE"
    assert manifest["cleanup_passed"] is True
    if inspection == "server":
        entries = manifest["server_inspect"]["Config"]["Env"]
    else:
        first = next(x for x in manifest["operations"]
                     if x["stage"] == "foundation")
        entries = first["inspect"]["Config"]["Env"]
    assert entries == [
        "POSTGRES_PASSWORD=<test-secret>", "SAFE_FEATURE_FLAG=enabled",
    ]
    assert hashlib.sha256(receipt.manifest_bytes).hexdigest() == receipt.sha256
    assert (evidence / "run-manifest.json").read_bytes() == receipt.manifest_bytes
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


# P1-F HIGH: a valid list of KEY=VALUE strings is not a confidentiality
# boundary. Test values AND key names split a generated disposable password.
# Keep all Docker/OS/PG interactions synthetic and the candidate command denied.
P1F_CASES = (
    "two_fragments", "four_fragments", "key_value_split",
    "complete_unexpected_name", "unexpected_nested_field",
    "unknown_proxy_value",
)
P1F_TARGETS = ("server", "foundation", "restart")


def _p1f_inspect_payload(case, secret):
    n = len(secret)
    if case == "two_fragments":
        env = ["PART_A=" + secret[:n//2], "PART_B=" + secret[n//2:]]
    elif case == "four_fragments":
        env = ["PART_%s=%s" % (i, secret[i*n//4:(i+1)*n//4])
               for i in range(4)]
    elif case == "key_value_split":
        # P1-E admitted keys of any nonempty text; here the key itself
        # includes the leading portion of a genuine generated password.
        env = ["PREFIX_" + secret[:n//2] + "=" + secret[n//2:]]
    elif case == "complete_unexpected_name":
        env = ["UNEXPECTED_FEATURE=" + secret]
    elif case == "unknown_proxy_value":
        env = ["HTTP_PROXY=" + secret[:n//2],
               "UNEXPECTED_SUFFIX=" + secret[n//2:]]
    elif case == "unexpected_nested_field":
        env = ["SAFE_FEATURE_FLAG=enabled", "PART_A=" + secret[:n//2]]
    else:
        raise AssertionError("unknown P1-F synthetic payload")
    response = [{"Config": {"Env": env}}]
    if case == "unexpected_nested_field":
        response[0]["EnvInspectExtra"] = {"unsanctioned": secret[n//2:]}
    return response


@pytest.mark.parametrize("target", P1F_TARGETS)
@pytest.mark.parametrize("case", P1F_CASES)
def test_p1_f_split_secret_cannot_appear_in_admitted_inspect_snapshot(
        system, monkeypatch, tmp_path, target, case):
    """Mock full successful run; inspect stdout contains generated secret shards.

    A safe outcome may be a fail-closed rejection, but the candidate projection
    must keep normal synthetic B5 runs admissible while publishing NO untrusted
    secret fragment, key or extra field from inspection.
    """
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1f-" + target + "-" + case)
    old_command = runner.command
    seen = {"count": 0, "secret": None, "shards": [], "record": None}

    def mocked_inspect(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"] and len(argv) == 3:
            name = argv[2]
            hit = ((target == "server" and name.endswith("-server"))
                   or (target == "foundation" and name.endswith("-foundation"))
                   or (target == "restart" and name.endswith("-restart")))
            if hit:
                record = runner._LIFECYCLE.get()
                secret_tuple = runner._TEST_SECRETS.get()
                assert record is not None and len(secret_tuple) == 4
                password = secret_tuple[0]
                raw_obj = _p1f_inspect_payload(case, password)
                seen.update(count=seen["count"] + 1, secret=password,
                            record=record, source=raw_obj)
                if case == "four_fragments":
                    seen["shards"] = [s.split("=", 1)[1] for s in raw_obj[0]["Config"]["Env"]]
                else:
                    seen["shards"] = [
                        password[:len(password)//2], password[len(password)//2:]]
                return SimpleNamespace(returncode=0,
                                       stdout=json.dumps(raw_obj), stderr="")
        return old_command(argv, **kwargs)

    monkeypatch.setattr(runner, "command", mocked_inspect)
    if case == "key_value_split":
        # New stricter ASCII key grammar rejects the key containing the
        # test-only password prefix. This is an ALLOWED fail-closed outcome,
        # not a failed projection or a missing admission test.
        with pytest.raises(RuntimeError,
                           match="inspection metadata schema invalid") as caught:
            consumer.run_actual_reporting_stage_with_bound_admission_candidate(
                allow=True, wheelhouse=tmp_path, evidence=evidence)
        assert seen["count"] == 1
        secret = seen["secret"]
        assert secret is not None and secret.startswith("test-only-")
        assert secret[:len(secret)//2] not in str(caught.value)
        assert secret[len(secret)//2:] not in str(caught.value)
        assert seen["record"]["result"] == "FAILED"
        assert seen["record"]["cleanup_passed"] is True
        assert seen["record"]["error_type"] == "RuntimeError"
        assert seen["record"]["credential_file_cleanup"] == (
            "NOT_PRESENT" if target == "server" else "REMOVED")
        assert not (evidence / "test-client.env").exists()
        terminal = evidence / "run-manifest.json"
        assert terminal.is_file()
        text = terminal.read_text(encoding="utf-8")
        assert json.loads(text)["result"] == "FAILED"
        assert secret[:len(secret)//2] not in text
        assert secret[len(secret)//2:] not in text
        assert admission.admit_archive_without_attestation_candidate(terminal) is None
        assert len(signals) == 2 and signals[-1] == "old"
        assert runner._LIFECYCLE.get() is None
        assert runner._TEST_SECRETS.get() == ()
        return

    receipt = consumer.run_actual_reporting_stage_with_bound_admission_candidate(
        allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert receipt is not None, "safe projected inspect must not need raw Env"
    assert seen["count"] == 1
    secret = seen["secret"]
    assert secret is not None and secret.startswith("test-only-")
    assert "".join(seen["shards"]) == secret
    result = receipt.manifest_bytes.decode("utf-8")
    assert secret not in result
    # The source fragments are long enough to be meaningful security leaks;
    # test each individually, not just contiguous full-secret occurrences.
    assert all(shard not in result for shard in seen["shards"] if shard)
    record = json.loads(result)
    assert record["result"] == "PASS"
    assert record["secret_redactions"] == 0
    assert record["cleanup_passed"] is True
    if target == "server":
        published = record["server_inspect"]
    else:
        op = next(x for x in record["operations"] if x["stage"] == target)
        published = op["inspect"]
    assert list(published) == ["Config"], (
        "no raw inspect metadata may cross the publication boundary")
    assert published["Config"]["Env"] in (
        [], ["SAFE_FEATURE_FLAG=enabled"],
        ["HTTP_PROXY=<redacted-env-value>"])
    assert all("PART_" not in x and "UNEXPECTED" not in x
               for x in published["Config"]["Env"])
    assert hashlib.sha256(receipt.manifest_bytes).hexdigest() == receipt.sha256
    assert (evidence / "run-manifest.json").read_bytes() == receipt.manifest_bytes
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("target", P1F_TARGETS)
def test_p1_f_duplicate_name_conflict_is_rejected_without_admission(
        system, monkeypatch, tmp_path, target):
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1f-duplicate-" + target)
    old_command = runner.command
    seen = {"secret": None, "count": 0}

    def duplicate(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"] and len(argv) == 3:
            name = argv[2]
            if name.endswith("-" + target):
                password = runner._TEST_SECRETS.get()[0]
                seen.update(secret=password, count=seen["count"] + 1)
                return SimpleNamespace(returncode=0, stdout=json.dumps([{
                    "Config": {"Env": [
                        "SAFE_FEATURE_FLAG=enabled",
                        "SAFE_FEATURE_FLAG=" + password,
                    ]}
                }]), stderr="")
        return old_command(argv, **kwargs)
    monkeypatch.setattr(runner, "command", duplicate)
    with pytest.raises(RuntimeError,
                       match="inspection metadata schema invalid") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert seen["count"] == 1
    assert seen["secret"] not in repr(caught.value)
    assert observed["stage_seen"] == []
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()


@pytest.mark.parametrize("target", ("server", "foundation"))
def test_p1_f_approved_safe_env_constants_and_untrusted_value_masked(
        system, monkeypatch, tmp_path, target):
    runner, admission, consumer, _stage, signals, observed = system
    evidence = tmp_path / ("p1f-safe-constants-" + target)
    old_command = runner.command
    seen = {"secret": None, "count": 0}

    def constants(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"] and len(argv) == 3:
            name = argv[2]
            hit = ((target == "server" and name.endswith("-server"))
                   or (target == "foundation" and name.endswith("-foundation")))
            if hit:
                password = runner._TEST_SECRETS.get()[0]
                seen.update(secret=password, count=seen["count"] + 1)
                return SimpleNamespace(returncode=0, stdout=json.dumps([{
                    "Config": {"Env": [
                        "POSTGRES_PASSWORD=" + password,
                        "SAFE_FEATURE_FLAG=enabled",
                        "HTTP_PROXY=",
                        "NAME=a=b",
                        "OTHER_NAME=" + password[:len(password)//2],
                    ]}, "Raw": {"untrusted-extra": password},
                }]), stderr="")
        return old_command(argv, **kwargs)

    monkeypatch.setattr(runner, "command", constants)
    receipt = consumer.run_actual_reporting_stage_with_bound_admission_candidate(
        allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert receipt is not None and seen["count"] == 1
    data = receipt.manifest_bytes.decode("utf-8")
    assert seen["secret"] not in data
    assert seen["secret"][:len(seen["secret"])//2] not in data
    manifest = json.loads(data)
    published = (manifest["server_inspect"] if target == "server"
                 else next(x["inspect"] for x in manifest["operations"]
                           if x["stage"] == target))
    assert published == {"Config": {"Env": [
        "POSTGRES_PASSWORD=<test-secret>",
        "SAFE_FEATURE_FLAG=enabled",
        "HTTP_PROXY=",
        "NAME=a=b",
    ]}}
    assert admission.admit_archive_without_attestation_candidate(
        evidence / "run-manifest.json") is None
    assert len(signals) == 2 and signals[-1] == "old"
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()
