"""Protected host principal -> exact scope -> SELECT-only read.

The host alone retains ReadAuthority and supplies authenticated principal scope.
This is NOT an authentication provider or a sandbox against host code/private-state
mutation. Only the service and original opaque handles go to a consumer. No SQL,
model/source loading, legacy fallback, grant consumption or write recovery occurs.
"""
from dataclasses import dataclass
import math
from threading import Lock
from time import time, monotonic
from src.data.contracts.canonical_forecast_read import (
    ExactForecastRead, CanonicalReadOutcome, CanonicalReadRecordSet,
)
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope, StoredForecastBatch
from src.database.forecast_read_connection import ForecastReadConnectionFactory
from src.providers.forecast_read_provider import ForecastReadProvider
from src.repositories.forecast_read_repository import ForecastReadRepository


class ForecastReadDenied(ValueError):
    pass


class HostReadPrincipal:
    __slots__ = ()
    def __reduce__(self):
        raise TypeError('original host principal only')


class ExactReadGrant:
    __slots__ = ()
    def __reduce__(self):
        raise TypeError('original read grant only')


@dataclass(frozen=True)
class _Principal:
    handle: object
    subject_reference: str
    scope_json: str
    expires: float
    end: float


@dataclass(frozen=True)
class _Grant:
    handle: object
    principal: object
    selection_json: str
    expires: float
    end: float


class _ReadState:
    def __init__(self, provider, operator, wall, elapsed):
        self.provider = self.pinned = provider
        self.repository = provider.repository
        self.reader = self.repository.reader
        self.scope = self.repository.scope.canonical_json
        self.operator = operator
        self.wall, self.elapsed = wall, elapsed
        self.lock = Lock()
        self.principals, self.grants, self.revoked = {}, {}, set()
        self.last = None

    def clocks(self, cancelled):
        # ALL supported callbacks before final private-record and owner checks.
        stop = cancelled(); elapsed = self.elapsed(); now = self.wall()
        if (stop is not False or any(type(v) not in (int, float) or not math.isfinite(v)
                                   for v in (now, elapsed))):
            raise ForecastReadDenied('read unavailable')
        with self.lock:
            if self.last is not None and (now < self.last[0] or elapsed < self.last[1]):
                raise ForecastReadDenied('read unavailable')
            self.last = (now, elapsed)
        return now, elapsed

    def principal(self, handle, now, elapsed):
        p = self.principals.get(id(handle))
        if (type(handle) is not HostReadPrincipal or p is None or p.handle is not handle
                or handle in self.revoked or not now < p.expires or not elapsed < p.end
                or self.provider is not self.pinned or self.provider.repository is not self.repository
                or self.repository.reader is not self.reader or self.repository.scope.canonical_json != self.scope
                or p.scope_json != self.scope):
            raise ForecastReadDenied('read unavailable')
        return p

    def require(self, handle, selection, cancelled):
        if type(selection) is not ExactForecastRead:
            raise ForecastReadDenied('read unavailable')
        text = ExactForecastRead.model_validate_json(selection.canonical_json).canonical_json
        now, elapsed = self.clocks(cancelled)
        if selection.canonical_json != text:
            raise ForecastReadDenied('read unavailable')
        with self.lock:
            g = self.grants.get(id(handle))
            if (type(handle) is not ExactReadGrant or g is None or g.handle is not handle
                    or handle in self.revoked or g.selection_json != text
                    or not now < g.expires or not elapsed < g.end):
                raise ForecastReadDenied('read unavailable')
            self.principal(g.principal, now, elapsed)
            if selection.scope.canonical_json != self.scope:
                raise ForecastReadDenied('read unavailable')
            return g


class ReadAuthority:
    """Host-only composition. Grants explicitly permit full lineage/receipt disclosure."""
    def __init__(self, state):
        self._state = state

    def establish_principal(self, operator, *, subject_reference, scope, lifetime_seconds=60):
        s = self._state
        if (operator is not s.operator or type(subject_reference) is not str
                or not 1 <= len(subject_reference) <= 256 or type(scope) is not ForecastStorageScope
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60
                or scope.canonical_json != s.scope):
            raise ForecastReadDenied('read unavailable')
        now, elapsed = s.clocks(lambda: False)
        h = HostReadPrincipal()
        with s.lock:
            s.principals[id(h)] = _Principal(h, subject_reference, scope.canonical_json,
                                           now+lifetime_seconds, elapsed+lifetime_seconds)
        return h

    def authorize(self, operator, principal, selection, *, lifetime_seconds=30):
        s = self._state
        if (operator is not s.operator or type(selection) is not ExactForecastRead
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60):
            raise ForecastReadDenied('read unavailable')
        text = ExactForecastRead.model_validate_json(selection.canonical_json).canonical_json
        now, elapsed = s.clocks(lambda: False)
        with s.lock:
            p = s.principal(principal, now, elapsed)
            if selection.scope.canonical_json != p.scope_json:
                raise ForecastReadDenied('read unavailable')
            h = ExactReadGrant()
            s.grants[id(h)] = _Grant(h, principal, text, min(p.expires, now+lifetime_seconds),
                                    min(p.end, elapsed+lifetime_seconds))
        return h

    def revoke(self, operator, handle):
        s = self._state
        if operator is not s.operator:
            raise ForecastReadDenied('read unavailable')
        with s.lock:
            record = s.principals.get(id(handle)) or s.grants.get(id(handle))
            if record is None or record.handle is not handle:
                raise ForecastReadDenied('read unavailable')
            s.revoked.add(handle)


class CanonicalForecastReadService:
    def __init__(self, state):
        self._state = state

    def read(self, grant, selection, *, cancelled=lambda: False):
        s = self._state
        try:
            original = s.require(grant, selection, cancelled)
        except Exception:
            return CanonicalReadOutcome(status='DENIED')
        status, records = 'UNAVAILABLE', None
        try:
            # Use retained immutable authorization, never a mutable caller alias.
            approved = ExactForecastRead.model_validate_json(original.selection_json)
            found = s.provider.lookup(approved.idempotency_id)
            if found is None:
                status = 'NOT_FOUND'
            else:
                found = StoredForecastBatch.model_validate_json(found.canonical_json)
                if (found.batch.scope != approved.scope or found.batch.idempotency_id != approved.idempotency_id
                        or found.batch.content_sha256 != approved.expected_content_sha256):
                    raise ValueError('unexpected content')
                records = CanonicalReadRecordSet(stored=found)
                records.display_values
                status = 'VERIFIED_RECORD_SET'
        except Exception:
            status, records = 'UNAVAILABLE', None
        # No content, NOT_FOUND or storage diagnostic escapes after final revocation.
        try:
            if s.require(grant, selection, cancelled) is not original:
                raise ForecastReadDenied('read unavailable')
        except Exception:
            return CanonicalReadOutcome(status='DENIED')
        return CanonicalReadOutcome(status=status, records=records)


def build_canonical_forecast_reader(reader, scope, *, operator, wall_clock=time, elapsed_clock=monotonic):
    """Trusted host bootstrap only; return authority separately from consumer service.

    Caller data must never select this factory, credential, operator or principal
    scope. Synthetic host contexts in tests are not production authentication.
    """
    if type(operator) is not object or not callable(wall_clock) or not callable(elapsed_clock):
        raise ForecastReadDenied('read unavailable')
    factory = ForecastReadConnectionFactory(reader)
    repository = ForecastReadRepository(factory, scope)
    state = _ReadState(ForecastReadProvider(repository), operator, wall_clock, elapsed_clock)
    return ReadAuthority(state), CanonicalForecastReadService(state)
