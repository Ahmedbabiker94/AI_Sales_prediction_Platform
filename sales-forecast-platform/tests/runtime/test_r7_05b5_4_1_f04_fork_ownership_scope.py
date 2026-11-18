"""F-04 behavioral ownership-boundary parity for review-only fork.

No real Docker calls, subprocesses, PostgreSQL, network or credentials.
Invariant: owned cleanup calls retain the lifecycle context, but final Docker
inventory queries and manifest publication must NOT inherit its registration
or timeout context; outer tokens must be restored on every exception.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    module = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(module, "verified_clean_source", lambda: "a" * 40)
    return module


@pytest.mark.parametrize("fault", [
    "normal", "listing_unavailable", "manifest_write_error"
])
def test_cleanup_context_only_active_for_owned_cleanup(
        fork, monkeypatch, tmp_path, fault):
    sentinel = {"nested_caller": True}
    original_token = fork._LIFECYCLE.set(sentinel)
    observed = []
    run = {}
    # Only mocked signal operations, never mutate the CI worker handler.
    monkeypatch.setattr(fork, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "original-handler",
        signal=lambda *args: observed.append("signal")))
    clock = [0.0]
    def monotonic():
        clock[0] += 1
        return clock[0]
    monkeypatch.setattr(fork, "time", SimpleNamespace(monotonic=monotonic,
                                                      sleep=lambda _: None))

    def pre_docker_stop(identity):
        record = fork._LIFECYCLE.get()
        assert record is not None and record is not sentinel
        run["id"] = record["run_id"]
        run["record"] = record
        raise RuntimeError("synthetic preflight stop: NO Docker")
    monkeypatch.setattr(fork, "inspect_image", pre_docker_stop)

    def fake_remove(kind, name, run_id):
        active = fork._LIFECYCLE.get()
        assert active is not None and active is not sentinel
        assert active["run_id"] == str(run_id)
        observed.append("owned_cleanup")
        return True
    monkeypatch.setattr(fork, "remove_owned", fake_remove)

    def fake_query(argv, **kwargs):
        # Accepted runner resets its lifecycle before these read-only queries.
        assert fork._LIFECYCLE.get() is None
        assert argv[0] == "docker"
        assert argv[1:] in (
            ["ps", "-aq", "--filter", "label=r7_05b3_run=" + run["id"]],
            ["volume", "ls", "-q", "--filter", "label=r7_05b3_run=" + run["id"]],
        )
        observed.append("inventory")
        if fault == "listing_unavailable":
            raise RuntimeError("synthetic listing unavailable")
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(fork, "command", fake_query)

    def fake_publish(path, value):
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get()
        observed.append("publish")
        if fault == "manifest_write_error":
            raise OSError("synthetic manifest write failed")
    monkeypatch.setattr(fork, "write_evidence", fake_publish)

    try:
        # A secondary manifest-write failure must not mask primary preflight.
        expected = RuntimeError
        with pytest.raises(expected):
            fork.run(allow=True, wheelhouse=tmp_path,
                     evidence=tmp_path / "evidence")
        assert fork._LIFECYCLE.get() is sentinel
        assert fork._TEST_SECRETS.get() == ()
        assert observed.count("inventory") == 2
        assert observed.count("publish") == 1
        if fault == "manifest_write_error":
            assert run["record"]["result"] == "FAILED"
            assert run["record"]["evidence_status"] == "EVIDENCE_UNAVAILABLE"
        if fault == "listing_unavailable":
            # A failed listing is fail-closed, not a PASS.
            assert "owned_cleanup" not in observed  # no resource was created
        assert observed.count("signal") == 2
    finally:
        fork._LIFECYCLE.reset(original_token)
        fork._TEST_SECRETS.set(())
    assert fork._LIFECYCLE.get() is None
