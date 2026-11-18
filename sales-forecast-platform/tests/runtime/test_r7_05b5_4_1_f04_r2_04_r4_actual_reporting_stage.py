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
