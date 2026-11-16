"""F-04 finalization failures, using an entirely mocked disposable runner.

These tests exercise run() through its actual finally path. No Docker daemon,
registry, database or model is touched. A successful test run is NOT approval
to perform a real disposable PostgreSQL run.
"""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres")


@pytest.mark.parametrize("case", [
    "unresolved_daemon",
    "foreign_or_unclean_resource",
    "late_owned_container",
    "unavailable_listing",
    "credential_unlink_unconfirmed",
    "unexpected_manifest_secret",
])
def test_run_finalization_fails_closed_on_negative_scenarios(
        runner, monkeypatch, tmp_path, case):
    """Prove the final manifest rejects unsafe outcomes, not only a helper."""
    evidence = tmp_path / "evidence"
    run_hex = uuid4().hex
    owned_name = "r7-05b3-" + run_hex + "-synthetic"
    created_record = {}
    removal_attempts = []
    queries = []

    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    # Replace only this module's time reference; do not alter the global time
    # module pytest itself uses. Accelerate the bounded ambiguity loop.
    tick = [0.0]

    def monotonic():
        tick[0] += 1.0
        return tick[0]

    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=monotonic, sleep=lambda _: None))

    def stop_before_docker(identity):
        lifecycle = runner._LIFECYCLE.get()
        created_record["value"] = lifecycle
        assert lifecycle is not None
        assert identity == runner.POSTGRES
        # Mirror an already registered daemon outcome. The failure happens
        # before build_client or a real Docker mutation.
        lifecycle["creation_attempts"].append({
            "kind": "container",
            "name": owned_name,
            "state": ("DAEMON_UNRESOLVED" if case == "unresolved_daemon"
                      else "RETURNED"),
        })
        if case == "unexpected_manifest_secret":
            lifecycle["unexpected_metadata"] = {
                "custom_field": runner._TEST_SECRETS.get()[0]}
        raise RuntimeError("synthetic preflight stop: no Docker used")

    monkeypatch.setattr(runner, "inspect_image", stop_before_docker)

    def fake_remove(kind, name, run_id):
        removal_attempts.append((kind, name, str(run_id)))
        assert kind == "container" and name == owned_name
        return case != "foreign_or_unclean_resource"

    monkeypatch.setattr(runner, "remove_owned", fake_remove)

    def fake_command(argv, **kwargs):
        queries.append(argv)
        assert argv[0] == "docker"
        assert argv[1:] in (
            ["ps", "-aq", "--filter", "label=r7_05b3_run=" + str(created_record["value"]["run_id"])],
            ["volume", "ls", "-q", "--filter", "label=r7_05b3_run=" + str(created_record["value"]["run_id"])],
        )
        if case == "unavailable_listing":
            raise RuntimeError("synthetic daemon query failure")
        if case == "late_owned_container" and argv[1] == "ps":
            return SimpleNamespace(stdout=owned_name)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(runner, "command", fake_command)
    if case == "credential_unlink_unconfirmed":
        monkeypatch.setattr(runner, "remove_transient_credential_file",
                            lambda path: "UNCONFIRMED")

    with pytest.raises(runner.CleanupUnconfirmed, match="CLEANUP_UNCONFIRMED"):
        runner.run(allow=True, wheelhouse=tmp_path, evidence=evidence)

    manifest_path = evidence / "run-manifest.json"
    assert manifest_path.is_file()
    raw = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_passed"] is False
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert manifest["error_type"] == "RuntimeError"
    assert manifest["possible_owned_containers"] == [owned_name]
    assert manifest["source_commit"] == "a" * 40
    assert removal_attempts
    assert len(queries) == 2
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()

    if case == "unresolved_daemon":
        assert manifest["creation_attempts"][0]["state"] == "DAEMON_UNRESOLVED"
        # Even a successful mock removal and empty lists cannot authorize PASS.
        assert all(x[0] == "container" for x in removal_attempts)
        assert not manifest["remaining_containers"]
    if case == "foreign_or_unclean_resource":
        assert manifest["remaining_containers"] == ""
    if case == "late_owned_container":
        assert manifest["remaining_containers"] == owned_name
    if case == "unavailable_listing":
        assert manifest["remaining_containers"] == "UNAVAILABLE"
        assert manifest["remaining_volumes"] == "UNAVAILABLE"
    if case == "credential_unlink_unconfirmed":
        assert manifest["credential_file_cleanup"] == "UNCONFIRMED"
    if case == "unexpected_manifest_secret":
        secret = manifest["unexpected_metadata"]["custom_field"]
        assert secret == "<redacted-disposable-secret>"
        assert manifest["secret_redactions"] >= 1
        assert "test-only-" not in raw
