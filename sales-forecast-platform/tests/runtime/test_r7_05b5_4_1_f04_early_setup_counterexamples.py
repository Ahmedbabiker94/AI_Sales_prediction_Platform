"""F-04 confirmed early-setup counterexamples in unmodified test runner.

Passing cases intentionally demonstrate a lifecycle ContextVar leak. They are
NOT security acceptance tests. No Docker/PostgreSQL operations are reached.
Tests restore original ContextVars in finally to avoid poisoning other cases.
"""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_postgres")


@pytest.mark.parametrize("failure_site", ["sigterm_install", "password_generation"])
def test_original_runner_early_setup_error_retains_lifecycle_context(
        runner, monkeypatch, tmp_path, failure_site):
    assert runner._LIFECYCLE.get() is None
    assert runner._TEST_SECRETS.get() == ()
    monkeypatch.setattr(runner, "verified_clean_source", lambda: "a" * 40)
    # Use a private mock signal object: never change the real CI SIGTERM handler.
    def reject_signal(*args):
        raise RuntimeError("synthetic signal registration failure")
    monkeypatch.setattr(runner, "signal", SimpleNamespace(
        SIGTERM=15, getsignal=lambda _: None,
        signal=(reject_signal if failure_site == "sigterm_install" else lambda *args: None)))
    if failure_site == "password_generation":
        def reject_password(*args):
            raise RuntimeError("synthetic password-generation failure")
        monkeypatch.setattr(runner.secrets, "token_hex", reject_password)
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            runner.run(allow=True, wheelhouse=tmp_path,
                       evidence=tmp_path / "evidence")
        # Intentionally a vulnerability demonstration, not a desired invariant.
        assert runner._LIFECYCLE.get() is not None
        assert runner._TEST_SECRETS.get() == ()
        assert not (tmp_path / "evidence" / "run-manifest.json").exists()
    finally:
        # Restore the test's own context; do not leave test data bound.
        runner._LIFECYCLE.set(None)
        runner._TEST_SECRETS.set(())
