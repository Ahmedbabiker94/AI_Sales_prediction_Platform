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
    with pytest.raises(RuntimeError, match="command details suppressed") as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert caught.value is expected_error
    assert caught.value.__cause__ is None
    assert captured["attempt"]["state"] == "DAEMON_UNRESOLVED"
    record = captured["record"]
    assert record["result"] == "FAILED"
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
def test_p1_partial_write_with_secondary_failure_keeps_original_exception(
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
    with pytest.raises(OSError) as caught:
        consumer.run_actual_reporting_stage_with_bound_admission_candidate(
            allow=True, wheelhouse=tmp_path, evidence=evidence)
    assert caught.value is primary, "secondary failure must not replace primary"
    assert attempted["secondary"], "fault injection must actually execute"
    assert len(observed["stage_seen"]) == 1
    name, record = observed["stage_seen"][0]
    assert record["result"] == "FAILED"
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
