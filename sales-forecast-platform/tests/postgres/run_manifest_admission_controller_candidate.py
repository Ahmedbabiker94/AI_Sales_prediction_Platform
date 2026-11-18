"""F-04 R2-04: review-only synchronous manifest admission controller.

This module owns a single candidate runner invocation. It does NOT accept a
caller-supplied "run_completed" flag. A successful synchronous run() return,
the run UUID observed from that very invocation, and a byte-for-byte match to
the run's retained finalized record are all necessary for admission.

NEVER run unmocked: run_postgres_security_patch_candidate.command() remains
unconditionally OFFLINE_REVIEW_ONLY, with no runnable CLI.

Not a durable or authenticated archive attestation. On restart, imported
files must NOT be admitted from a raw PASS manifest without a separately
reviewed signed/trusted receipt protocol. Host filesystem/TOCTOU and crash
durability assumptions remain outside the demonstrated synthetic proof.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from run_postgres_security_patch_candidate import run as _candidate_run


_MAX_MANIFEST_BYTES = 1_048_576


@dataclass(frozen=True)
class VerifiedCandidateManifest:
    """Immutable in-process evidence snapshot, not an archive receipt."""
    run_id: str
    sha256: str
    manifest_bytes: bytes
    source_commit: str


def _read_manifest_bytes(path):
    """Read exactly once from a no-follow regular-file descriptor (Linux only).

    This is a trusted-host candidate, not protection against hostile ancestor
    replacement or malicious filesystem behavior. Never re-open by path to
    validate the bytes a second time.
    """
    if not isinstance(path, Path):
        return None
    try:
        if (path.name != 'run-manifest.json' or path.is_symlink()
                or path.absolute() != path.resolve()
                or not hasattr(os, 'O_NOFOLLOW')):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            content = stream.read(_MAX_MANIFEST_BYTES + 1)
        if len(content) > _MAX_MANIFEST_BYTES:
            return None
        return content
    except (OSError, ValueError, TypeError):
        return None


def _valid_terminal_state(record, run_id):
    """Reject wrong invocation identity and nonterminal/ambiguous cleanup."""
    if type(record) is not dict or type(run_id) is not UUID:
        return False
    return (
        record.get('run_id') == str(run_id)
        and record.get('run_hex') == run_id.hex
        and record.get('result') == 'PASS'
        and record.get('evidence_status') == 'AVAILABLE'
        and record.get('cleanup_passed') is True
        and record.get('cleanup_status') == 'CLEANUP_SUCCESS'
        and record.get('handler_restoration') == 'RESTORED'
        and type(record.get('credential_file_cleanup')) is str
        and record['credential_file_cleanup'] in ('REMOVED', 'NOT_PRESENT')
        and type(record.get('secret_redactions')) is int
        and record['secret_redactions'] == 0
        and record.get('native_execution') is False
        and record.get('production_connections') is False
        and type(record.get('source_commit')) is str
        and len(record['source_commit']) in (40, 64)
    )


def invoke_candidate_and_admit(*, allow, wheelhouse, evidence, _host_stage=None):
    """Run once and admit ONLY its matching finalized, redacted byte snapshot.

    A separately supplied True, a prior run's result, and a bare PASS file
    cannot influence the decision. Exceptions from the runner (including
    KeyboardInterrupt and SystemExit) propagate: no result is issued.
    _host_stage is a TRUSTED in-process extension owned by the controller
    caller (e.g. a B5-4-1 reporting stage), never input from an archive.
    """
    if not isinstance(evidence, Path):
        raise TypeError('controller requires pathlib evidence directory')
    if _host_stage is not None and not callable(_host_stage):
        raise TypeError('host callback must be trusted callable')
    captured = []

    def stage(**kwargs):
        # The callback is invoked from the same runner we're controlling.
        # Capture its own identity and the SAME dict reference finalized later.
        run_id = kwargs.get('run_id')
        record = kwargs.get('record')
        envfile = kwargs.get('envfile')
        if (captured or type(run_id) is not UUID or type(record) is not dict
                or not isinstance(envfile, Path)
                or envfile != evidence / 'test-client.env'):
            raise RuntimeError('untrusted or duplicate run stage identity')
        captured.append((run_id, record))
        if _host_stage is not None:
            _host_stage(**kwargs)

    # This exact call is the ONLY source of completion. If it raises or is
    # interrupted, NO receipt is created, regardless of leftover file status.
    _candidate_run(allow=allow, wheelhouse=wheelhouse, evidence=evidence,
                   _host_stage=stage)

    if len(captured) != 1:
        return None
    run_id, record = captured[0]
    if not _valid_terminal_state(record, run_id):
        return None

    # R2-03 guarantees the original retained dict reference is replaced by
    # the *finalized redacted* snapshot before publication. Reconstruct those
    # expected canonical bytes from that in-memory run state, never from the
    # untrusted file that a consumer is trying to admit.
    try:
        expected = json.dumps(record, indent=2, sort_keys=True,
                              allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, UnicodeError):
        return None
    if len(expected) > _MAX_MANIFEST_BYTES:
        return None
    expected_digest = hashlib.sha256(expected).hexdigest()
    actual = _read_manifest_bytes(evidence / 'run-manifest.json')
    if actual is None or len(actual) != len(expected):
        return None
    if hashlib.sha256(actual).hexdigest() != expected_digest or actual != expected:
        return None
    # Return only a pinned immutable copy. Consumers must use this verified
    # snapshot, not re-read an untrusted path that may change later.
    return VerifiedCandidateManifest(
        run_id=str(run_id), sha256=expected_digest,
        manifest_bytes=actual, source_commit=record['source_commit'])


def admit_archive_without_attestation_candidate(_path):
    """Explicit default-deny archive/import boundary.

    A raw manifest is insufficient after the synchronous controller exits.
    No authenticated durable receipt, replay policy, restart recovery, or
    fsync protocol is implemented here; therefore archive admission is False.
    """
    return None
