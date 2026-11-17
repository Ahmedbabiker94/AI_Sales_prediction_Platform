"""Offline candidate-only scope exception matrix; NOT accepted runner tests."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def scope_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("run_context_scope_candidate")


@pytest.mark.parametrize("fault", [
    "none", "install", "generate", "body", "restore", "partial_install",
])
def test_context_and_handler_restored_on_stage_failure(scope_module, fault):
    events = []
    initial_record = {"prior": "record"}
    original = ("prior-test-secret",)
    lifetime_token = scope_module.REVIEW_LIFECYCLE.set(initial_record)
    secrets_token = scope_module.REVIEW_SECRETS.set(original)
    now_record = {"new": True}

    def install():
        events.append("install")
        if fault in ("install", "partial_install"):
            raise RuntimeError("synthetic installation failure")

    def restore():
        events.append("restore")
        if fault == "restore":
            raise RuntimeError("synthetic restoration failure")

    def generate():
        events.append("generate")
        if fault == "generate":
            raise RuntimeError("synthetic generation failure")
        return ("test-only-" + "a" * 40,)

    try:
        def exercise():
            with scope_module.protected_scope_candidate(
                now_record, install_handler=install, restore_handler=restore,
                make_secrets=generate,
            ) as generated:
                assert generated == ("test-only-" + "a" * 40,)
                assert scope_module.REVIEW_LIFECYCLE.get() is now_record
                assert scope_module.REVIEW_SECRETS.get() == generated
                events.append("body")
                if fault == "body":
                    raise RuntimeError("synthetic action failure")
        if fault == "none":
            exercise()
        else:
            with pytest.raises(RuntimeError, match="synthetic"):
                exercise()
        assert scope_module.REVIEW_LIFECYCLE.get() is initial_record
        assert scope_module.REVIEW_SECRETS.get() == original
        # Never leave a changed SIGTERM handler un-restored even if the
        # installation attempt itself raised after partially mutating state.
        assert events.count("restore") == 1
    finally:
        scope_module.REVIEW_SECRETS.reset(secrets_token)
        scope_module.REVIEW_LIFECYCLE.reset(lifetime_token)
    assert scope_module.REVIEW_LIFECYCLE.get() is None
    assert scope_module.REVIEW_SECRETS.get() == ()


@pytest.mark.parametrize("bad", [None, [], 1, {}, {"ok": "yes"}])
def test_invalid_context_inputs_reject_without_changing_context(scope_module, bad):
    existing = scope_module.REVIEW_LIFECYCLE.get()
    previous_secrets = scope_module.REVIEW_SECRETS.get()
    if type(bad) is dict:
        # Dicts are valid records; failures must arise during setup instead.
        def invalid_gen():
            return []
        with pytest.raises(ValueError, match="invalid generated"):
            with scope_module.protected_scope_candidate(
                bad, install_handler=lambda: None,
                restore_handler=lambda: None, make_secrets=invalid_gen,
            ):
                pass
    else:
        with pytest.raises(ValueError, match="invalid lifecycle"):
            with scope_module.protected_scope_candidate(
                bad, install_handler=lambda: None,
                restore_handler=lambda: None,
                make_secrets=lambda: ("test-only-x",),
            ):
                pass
    assert scope_module.REVIEW_LIFECYCLE.get() is existing
    assert scope_module.REVIEW_SECRETS.get() == previous_secrets


def test_restoration_failure_does_not_prevent_context_token_reset(scope_module):
    old = scope_module.REVIEW_LIFECYCLE.get()
    with pytest.raises(RuntimeError, match="restore broke"):
        with scope_module.protected_scope_candidate(
            {}, install_handler=lambda: None,
            restore_handler=lambda: (_ for _ in ()).throw(
                RuntimeError("restore broke")),
            make_secrets=lambda: ("test-only-" + "e" * 40,),
        ):
            pass
    assert scope_module.REVIEW_LIFECYCLE.get() is old
    assert scope_module.REVIEW_SECRETS.get() == ()
