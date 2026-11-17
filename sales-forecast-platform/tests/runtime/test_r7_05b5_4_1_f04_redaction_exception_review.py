"""Additional pure F-04 redaction finalizer error-path review.

These tests do not claim that protected run_postgres.py is corrected.
No Docker, PostgreSQL, network, credential files or runner mutation.
"""
from contextvars import ContextVar
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def proposal(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("redaction_finalization_candidate")


@pytest.mark.parametrize("bad", [
    {"cleanup_passed": True, "unexpected": object()},
    {"cleanup_passed": True, "unexpected": {1, 2}},
    {"cleanup_passed": True, "unexpected": float("nan")},
])
def test_unserializable_or_nonstandard_metadata_fails_without_manifest(
        proposal, bad):
    # A fail-closed exception, not a synthetic PASS artifact.
    # json.dumps allows NaN by default; this explicit test is intentionally
    # RED until the pure candidate rejects nonstandard JSON.
    with pytest.raises((TypeError, ValueError)):
        proposal.finalize_manifest_candidate(
            bad, ("test-only-" + "a" * 40,))


def test_candidate_does_not_leak_or_change_external_contextvar_on_error(proposal):
    active = ContextVar("candidate_external_secret_context", default=None)
    token = active.set(("test-only-" + "a" * 40,))
    try:
        with pytest.raises(TypeError):
            proposal.finalize_manifest_candidate(
                {"cleanup_passed": True, "unexpected": object()}, active.get())
        assert active.get() == ("test-only-" + "a" * 40,)
    finally:
        active.reset(token)
    assert active.get() is None


def test_candidate_json_serialization_cannot_mark_previous_failure_as_pass(proposal):
    secret = "test-only-" + "a" * 40
    with pytest.raises(TypeError):
        proposal.finalize_manifest_candidate(
            {"cleanup_passed": False, "cleanup_status": "CLEANUP_UNCONFIRMED",
             "unexpected": object()}, (secret,))
