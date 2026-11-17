"""Observed counterexample, NOT evidence the accepted runner is safe.

Run accepted runner with mocked pre-Docker stop; inject unserializable
manifest metadata, verify credentials ContextVar is left bound after failure.
This deliberately confirms a bug and MUST be replaced by a fail-closed
regression after separately authorized protected runner rebaseline.
Never executes Docker, PostgreSQL, registry, network or real credentials.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres")


def test_counterexample_manifest_serialization_exception_leaves_secret_context_bound(
        runner, monkeypatch, tmp_path):
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    clock = [0.0]

    def monotonic():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=monotonic, sleep=lambda _: None))
    observed = {}

    def pre_docker_stop(_):
        record = runner._LIFECYCLE.get()
        assert record is not None
        observed["run_id"] = record["run_id"]
        # No filesystem or daemon mutation; force JSON to fail late.
        record["invalid_manifest_metadata"] = object()
        raise RuntimeError("mock stop without Docker")

    monkeypatch.setattr(runner, "inspect_image", pre_docker_stop)
    monkeypatch.setattr(runner, "remove_owned", lambda *args: True)

    def fake_command(argv, **kwargs):
        assert argv[0] == "docker"
        assert argv[1:] in (
            ["ps", "-aq", "--filter", "label=r7_05b3_run=" + str(observed["run_id"])],
            ["volume", "ls", "-q", "--filter", "label=r7_05b3_run=" + str(observed["run_id"])],
        )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(runner, "command", fake_command)
    try:
        with pytest.raises(TypeError, match="not JSON serializable"):
            runner.run(
                allow=True, wheelhouse=tmp_path, evidence=tmp_path / "evidence")
        # Counterexample: this assertion is a demonstration of a defect.
        assert runner._LIFECYCLE.get() is None
        assert len(runner._TEST_SECRETS.get()) == 4
        assert not (tmp_path / "evidence" / "run-manifest.json").exists()
    finally:
        # Test-only cleanup of the deliberately exposed ContextVar leak.
        runner._TEST_SECRETS.set(())
    assert runner._TEST_SECRETS.get() == ()
