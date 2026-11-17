"""F-04 candidate-only secret-redaction finalization contract.

Pure in-memory proposal; NOT imported by, or wired to, run_postgres.py.
DO NOT use this module for actual runtime evidence without approved review.
"""
import json

REPLACEMENT = "<redacted-disposable-secret>"


def _redact(text, secrets):
    if type(text) is not str or type(secrets) not tuple:
        raise ValueError("invalid redaction inputs")
    if any(type(secret) is not str or not secret for secret in secrets):
        raise ValueError("invalid disposable secret")
    # The original runner uses distinct generated secrets. Disallow ambiguity
    # here; overlapping secrets would otherwise produce order-dependent counts.
    if len(set(secrets)) != len(secrets) or any(
        left in right for left in secrets for right in secrets if left != right
    ):
        raise ValueError("ambiguous disposable secrets")
    hits = 0
    for secret in secrets:
        hits += text.count(secret)
        text = text.replace(secret, REPLACEMENT)
    return text, hits


def finalize_manifest_candidate(record, secrets):
    """Return (redacted_manifest_json, failed) without any context variable.

    This intentionally models a *candidate proposal*: redact, count on actual
    serialized bytes before status decision, then serialize again. The caller
    must ensure trusted generated disposable secrets. No external writes.
    """
    if type(record) is not dict:
        raise ValueError("invalid lifecycle record")
    if record.get("cleanup_passed") is not True and record.get("cleanup_passed") is not False:
        raise ValueError("missing cleanup state")
    if type(record.get("secret_redactions", 0)) is not int or record.get("secret_redactions", 0) < 0:
        raise ValueError("invalid previous redaction count")
    snapshot = dict(record)
    raw = json.dumps(snapshot, indent=2, sort_keys=True)
    _, hits = _redact(raw, secrets)
    snapshot["secret_redactions"] = snapshot.get("secret_redactions", 0) + hits
    if snapshot["secret_redactions"]:
        snapshot["cleanup_passed"] = False
        snapshot["cleanup_status"] = "CLEANUP_UNCONFIRMED"
    if not snapshot["cleanup_passed"]:
        snapshot["result"] = "FAILED"
    final, additional = _redact(json.dumps(snapshot, indent=2, sort_keys=True), secrets)
    if additional:
        # No silently missed/new secrets after status decision.
        raise ValueError("unstable redaction after finalization")
    return final, not snapshot["cleanup_passed"]
