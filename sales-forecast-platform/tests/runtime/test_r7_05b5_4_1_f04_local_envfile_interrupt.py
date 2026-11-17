"""Local filesystem contract for interrupted review-fork cleanup.

Synthetic disposable bytes only; temporary test directory only. The accepted
runner is unchanged. No Docker, PostgreSQL, network, or subprocess execution.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def fork(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    model = importlib.import_module("run_postgres_security_patch_candidate")
    monkeypatch.setattr(model, "verified_clean_source", lambda: "a" * 40)
    monkeypatch.setattr(model, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: "old", signal=lambda *a: None))
    tick = [0.0]

    def monotonic():
        tick[0] += 0.1
        return tick[0]

    def stop_reconciliation_sleep(_):
        raise KeyboardInterrupt("synthetic reconciliation interrupted")

    monkeypatch.setattr(model, "time", SimpleNamespace(
        monotonic=monotonic, sleep=stop_reconciliation_sleep))
    monkeypatch.setattr(model, "remove_owned", lambda *a: True)
    return model


@pytest.mark.parametrize("kind", ["regular", "symlink"])
def test_real_local_envfile_unlink_on_interrupted_cleanup(
        fork, monkeypatch, tmp_path, kind):
    evidence = tmp_path / "evidence"
    owned_path = evidence / "test-client.env"
    target = tmp_path / "outside-fixture-do-not-delete.txt"
    target.write_text("synthetic-data-not-a-password", encoding="utf-8")
    observed = {}

    def synthetic_preflight(_):
        record = fork._LIFECYCLE.get()
        observed["record"] = record
        if kind == "regular":
            owned_path.write_text("synthetic-test-only-string", encoding="utf-8")
        else:
            owned_path.symlink_to(target)
        record["creation_attempts"].append({
            "kind": "container",
            "name": "r7-05b3-" + record["run_hex"] + "-synthetic",
            "state": "DAEMON_UNRESOLVED",
        })
        raise RuntimeError("synthetic preflight, no docker")

    monkeypatch.setattr(fork, "inspect_image", synthetic_preflight)
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic reconciliation"):
            fork.run(allow=True, wheelhouse=tmp_path, evidence=evidence)
        if kind == "regular":
            assert not owned_path.exists()
            assert observed["record"]["credential_file_cleanup"] == "REMOVED"
        else:
            assert owned_path.is_symlink()
            assert observed["record"]["credential_file_cleanup"] == "UNCONFIRMED"
            assert observed["record"]["cleanup_passed"] is False
            assert observed["record"]["cleanup_status"] == "CLEANUP_UNCONFIRMED"
        assert target.read_text(encoding="utf-8") == "synthetic-data-not-a-password"
        assert fork._LIFECYCLE.get() is None
        assert fork._TEST_SECRETS.get() == ()
        assert not (evidence / "run-manifest.json").exists()
    finally:
        # Do not leave test-owned symlink or local synthetic data behind.
        owned_path.unlink(missing_ok=True)
        fork._LIFECYCLE.set(None)
        fork._TEST_SECRETS.set(())
