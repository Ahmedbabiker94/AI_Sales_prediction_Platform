"""Isolated, pure redaction-design regressions; accepted runner remains RED.

No Docker, PostgreSQL, files, network or production secret values.
"""
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "postgres"))
    return importlib.import_module("redaction_finalization_candidate")


@pytest.mark.parametrize("field", [
    "unexpected_metadata",
    "operations",
    "creation_attempts",
    "unexpected_key",
])
def test_secret_in_any_serialized_field_fails_closed(candidate, field):
    secret = "test-only-" + "a" * 40
    record = {"result": "PASS", "cleanup_passed": True,
              "cleanup_status": "CLEANUP_SUCCESS"}
    if field == "unexpected_metadata":
        record[field] = {"custom_field": secret}
    elif field == "operations":
        record[field] = [{"nested": ["prefix:" + secret + ":suffix"]}]
    elif field == "creation_attempts":
        record[field] = [{"name": secret}]
    else:
        record[secret] = "unexpected value"
    original = json.dumps(record)
    raw, failed = candidate.finalize_manifest_candidate(record, (secret,))
    manifest = json.loads(raw)
    assert failed is True
    assert manifest["result"] == "FAILED"
    assert manifest["cleanup_passed"] is False
    assert manifest["cleanup_status"] == "CLEANUP_UNCONFIRMED"
    assert manifest["secret_redactions"] == 1
    assert secret not in raw
    assert json.dumps(record) == original  # pure; no caller mutation


def test_multiple_occurrences_and_secrets_counted(candidate):
    one = "test-only-" + "1" * 40
    two = "test-only-" + "2" * 40
    raw, failed = candidate.finalize_manifest_candidate(
        {"result": "PASS", "cleanup_passed": True,
         "unexpected_metadata": [one, "repeat " + one, two]},
        (one, two))
    obj = json.loads(raw)
    assert failed and obj["secret_redactions"] == 3
    assert one not in raw and two not in raw


def test_clean_manifest_does_not_trigger_false_failure(candidate):
    raw, failed = candidate.finalize_manifest_candidate(
        {"result": "PASS", "cleanup_passed": True,
         "cleanup_status": "CLEANUP_SUCCESS"}, ("test-only-" + "a" * 40,))
    obj = json.loads(raw)
    assert failed is False
    assert obj["result"] == "PASS"
    assert obj["cleanup_passed"] is True
    assert obj["secret_redactions"] == 0


def test_existing_failed_cleanup_never_becomes_success(candidate):
    secret = "test-only-" + "a" * 40
    raw, failed = candidate.finalize_manifest_candidate(
        {"result": "PASS", "cleanup_passed": False,
         "cleanup_status": "CLEANUP_UNCONFIRMED"}, (secret,))
    assert failed is True
    assert json.loads(raw)["result"] == "FAILED"


def test_previously_detected_secret_remains_failure(candidate):
    secret = "test-only-" + "a" * 40
    raw, failed = candidate.finalize_manifest_candidate(
        {"result": "PASS", "cleanup_passed": True, "secret_redactions": 2},
        (secret,))
    assert failed is True
    obj = json.loads(raw)
    assert obj["secret_redactions"] == 2
    assert obj["cleanup_status"] == "CLEANUP_UNCONFIRMED"


@pytest.mark.parametrize("invalid", [(), ("",), ("duplicate", "duplicate"),
                                      ("short", "prefix-short-suffix"), ["bad"]])
def test_ambiguous_or_invalid_secret_inventory_rejected(candidate, invalid):
    # Empty tuple is allowed when the controlled test has no generated
    # credentials yet; every other bad shape must be rejected.
    if invalid == ():
        _, failed = candidate.finalize_manifest_candidate(
            {"cleanup_passed": True}, ())
        assert failed is False
    else:
        with pytest.raises(ValueError):
            candidate.finalize_manifest_candidate({"cleanup_passed": True}, invalid)


@pytest.mark.parametrize("value", [None, "", [], {"secret_redactions": -1, "cleanup_passed": True},
                                      {"secret_redactions": "1", "cleanup_passed": True}])
def test_incomplete_or_malformed_lifecycle_rejected(candidate, value):
    with pytest.raises(ValueError):
        candidate.finalize_manifest_candidate(value, ("test-only-" + "a" * 40,))


def test_contextvar_lifetime_is_not_a_prerequisite(candidate):
    from contextvars import ContextVar
    lifecycle = ContextVar("test_lifecycle", default=None)
    token = lifecycle.set({"irrelevant": True})
    lifecycle.reset(token)
    assert lifecycle.get() is None
    secret = "test-only-" + "a" * 40
    raw, failed = candidate.finalize_manifest_candidate(
        {"cleanup_passed": True, "unexpected_metadata": {"secret": secret}},
        (secret,))
    assert failed and json.loads(raw)["secret_redactions"] == 1
