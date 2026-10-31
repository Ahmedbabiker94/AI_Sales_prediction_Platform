"""Protected host principal -> exact scope -> SELECT-only read.

The host alone retains ReadAuthority and supplies authenticated principal scope.
This is NOT an authentication provider or a sandbox against host code/private-state
mutation. Only the service and original opaque handles go to a consumer. No SQL,
model/source loading, legacy fallback, grant consumption or write recovery occurs.
"""
from src.providers._forecast_read_order import _ORDER, _operation, _activate_original, _issued_original, OrderDenied, _register_origin, _BOOTSTRAP_CREDENTIAL
from dis import Bytecode, get_instructions
from dataclasses import dataclass
import math
from threading import RLock, get_ident
from sys import _getframe, _current_frames, implementation, version_info
from weakref import WeakValueDictionary, ref
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


# These are private synchronization mechanics, not an authorization callback API.
# Every body passed below is a fixed local function in these two reviewed host
# services; consumers cannot select a body, lock, baseline or release operation.
_NATIVE_RLOCK = type(RLock())
_SUPPORTED_JOINT_RUNTIME = implementation.name == 'cpython' and version_info[:2] in ((3, 11), (3, 12))


@dataclass(frozen=True)
class _LockAttempt:
    lock: object
    baseline: int
    thread: int


def _release_owned(attempts):
    # No blocking acquire during cleanup. A pending/foreign-thread acquisition
    # has current-thread depth zero and is NEVER released by this operation.
    for attempt in reversed(attempts):
        if attempt.thread != get_ident():
            raise ForecastReadDenied('lock ownership unavailable')
        lock, baseline = attempt.lock, attempt.baseline
        depth = lock._recursion_count()
        if depth == baseline + 1:
            lock.release()
        elif depth != baseline:
            raise ForecastReadDenied('lock ownership unavailable')


class _HostHealth:
    """Process-owned fail-stop gate; never supplied by a consumer or reset.

    Publish an obligation BEFORE acquiring anything. Until a verified cleanup
    marks it safe, it is a pre-armed shutdown requirement if its original executor
    unwinds. A traceback is not a live stack. Inspection never takes an authority
    lock, and a signal interrupting inspection cannot remove the obligation.
    The whole protected reader host process is the quarantine/restart domain.
    """
    def __init__(self):
        self._obligations = {}
        self._shutdown = False

    def status(self):
        if self._shutdown or _ORDER.terminal():
            return 'SHUTDOWN_REQUIRED'
        frames = _current_frames()
        for operation in tuple(self._obligations.values()):
            if operation.safe:
                # A verified primary pass can leave only harmless bookkeeping
                # if the backup pass itself is interrupted. No native obligation
                # remains; prune without relying on GC/finalizers.
                self._obligations.pop(id(operation), None)
                continue
            frame = frames.get(operation.thread)
            while frame is not None and frame is not operation.frame:
                frame = frame.f_back
            if frame is None:
                # Terminal, no reset. Even interruption before this assignment
                # leaves the prepublished orphan as permanent fail-stop evidence.
                _ORDER.quarantine()
                self._shutdown = True
                return 'SHUTDOWN_REQUIRED'
        return 'HEALTHY'

    def require(self):
        if self.status() != 'HEALTHY':
            raise ForecastReadDenied('host shutdown required')


@dataclass
class _LockOperation:
    frame: object
    thread: int
    attempts: tuple
    safe: bool = False


# Host-private singleton deliberately covers every reader/label authority in this
# process, including attempts to construct a replacement authority. Recovery is a
# clean process restart, never another context, a caller flag or manual unlocking.
_HOST_HEALTH = _HostHealth()


def _locked_call(locks, body):
    # Prepare the complete immutable release ledger BEFORE any acquire. Native
    # RLock owner/depth changes linearize in C, including acquire-return signals.
    # A returned acquired flag is deliberately not the source of ownership.
    _HOST_HEALTH.require()
    if any(type(lock) is not _NATIVE_RLOCK for lock in locks) or len(set(locks)) != len(locks):
        raise ForecastReadDenied('lock ownership unavailable')
    attempts = tuple(_LockAttempt(lock, lock._recursion_count(), get_ident()) for lock in locks)
    operation = _LockOperation(_getframe(), get_ident(), attempts)
    try:
        try:
            # Registration is atomic under the supported CPython GIL and occurs
            # inside BOTH prepared cleanup regions, before native acquisition.
            _HOST_HEALTH._obligations[id(operation)] = operation
            for attempt in attempts:
                lock, baseline = attempt.lock, attempt.baseline
                if not lock.acquire(timeout=1.0):
                    raise ForecastReadDenied('lock acquisition unavailable')
                if lock._recursion_count() != baseline + 1:
                    raise ForecastReadDenied('lock ownership unavailable')
            result = body()
        finally:
            _release_owned(attempts)
            operation.safe = True
    finally:
        # Idempotent second release pass is protected before acquisition too.
        # If one interruption hits primary cleanup, the outer finally retries
        # using native current ownership. If it hits this pass alone, primary
        # cleanup has already released everything. Never swallow BaseException.
        _release_owned(attempts)
        operation.safe = True
        _HOST_HEALTH._obligations.pop(id(operation), None)
    _HOST_HEALTH.require()
    return result


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


class HostReadClock:
    """Host-pinned clock domain; fixed callback-free sampling, never permission.

    Explicit synthetic counters are controlled-offline TEST data. System mode
    samples captured builtins. No caller-selected sampler is accepted. Joint
    grants must use this domain from issuance; legacy grants are never rebound.
    """
    __slots__ = ('_operator', '_lock', '_synthetic', '_wall', '_elapsed', '_last')

    def __init__(self, operator, *, synthetic_start=None):
        if type(operator) is not object:
            raise ForecastReadDenied('read unavailable')
        self._operator, self._lock, self._last = operator, RLock(), None
        self._synthetic = synthetic_start is not None
        if self._synthetic:
            if type(synthetic_start) is not tuple or len(synthetic_start) != 2:
                raise ForecastReadDenied('read unavailable')
            self._wall, self._elapsed = synthetic_start
        else:
            self._wall = self._elapsed = None
        self.sample()

    def __reduce__(self):
        raise TypeError('original host clock only')

    def set_synthetic(self, operator, *, wall, elapsed):
        if operator is not self._operator or not self._synthetic:
            raise ForecastReadDenied('read unavailable')
        # Invalid/backward host-clock observations fail closed at sampling.
        def _under_lock_1():
            self._wall, self._elapsed = wall, elapsed
        _locked_call((self._lock,), _under_lock_1)

    def _sample_locked(self):
        now, elapsed = ((self._wall, self._elapsed) if self._synthetic else
                        (_SYSTEM_WALL(), _SYSTEM_ELAPSED()))
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (now, elapsed))
                or self._last is not None and (now < self._last[0] or elapsed < self._last[1])):
            raise ForecastReadDenied('read unavailable')
        self._last = now, elapsed
        return now, elapsed

    def sample(self):
        def _under_lock_2():
            return HostReadClock._sample_locked(self)
        return _locked_call((self._lock,), _under_lock_2)

    def wall(self):
        return self.sample()[0]

    def elapsed(self):
        return self.sample()[1]


_SYSTEM_WALL, _SYSTEM_ELAPSED = time, monotonic


class JointReadCheckpoint:
    """Opaque original handle; a constructed/serialized declaration is not issued."""
    __slots__ = ()
    def __reduce__(self):
        raise TypeError('original joint checkpoint only')


class _JointIssuer(JointReadCheckpoint):
    __slots__ = ()


@dataclass(frozen=True)
class _JointBinding:
    handle: object
    peer: object
    forecast_domain: object
    label_domain: object


class _JointLifetime:
    """Host-private, thread/stack-scoped cleanup ownership; never read authority.

    Enter BEFORE issuance. No protected record exists during context handoff.
    The frame identity is non-owning: retaining a traceback cannot keep the
    activation live. An interrupted close may defer physical pruning, but an
    unwound activation cannot validate even if its objects are retained.
    """
    def __init__(self, state, issuer):
        self.state, self.issuer = state, issuer
        self.record = None
        self.entered = self.closed = False
        self.frame_id = self.thread = None
        self.body_offsets = frozenset()

    def __reduce__(self):
        raise TypeError('original joint lifetime only')

    def __enter__(self):
        if not _SUPPORTED_JOINT_RUNTIME or self.entered or self.closed:
            raise ForecastReadDenied('read unavailable')
        frame = _getframe(1)
        instructions = tuple(get_instructions(frame.f_code, show_caches=True))
        entry = next((i for i in instructions if i.offset == frame.f_lasti), None)
        if entry is None or entry.opname != 'BEFORE_WITH' or entry.positions.col_offset is None:
            raise ForecastReadDenied('read unavailable')
        # CPython 3.11+ compiler positions distinguish the with BODY from its
        # exit machinery, even if an interrupted __exit__ is caught in the same
        # caller frame. Unsupported/missing position metadata fails closed.
        region = next((e for e in Bytecode(frame.f_code).exception_entries
                       if e.start == entry.offset + 2), None)
        if region is None:
            raise ForecastReadDenied('read unavailable')
        self.body_offsets = frozenset(i.offset for i in instructions if
            any(e.target == region.target and e.start <= i.offset < e.end
                for e in Bytecode(frame.f_code).exception_entries)
            and i.positions.lineno is not None and i.positions != entry.positions)
        self.frame_id, self.thread = id(frame), get_ident()
        self.entered = True
        return self

    def live(self):
        if not self.entered or self.closed or self.thread != get_ident():
            return False
        frame = _getframe(1)
        while frame is not None:
            if (id(frame) == self.frame_id and frame.f_lasti in self.body_offsets
                    and any(v is self for v in frame.f_locals.values())):
                return True
            frame = frame.f_back
        return False

    def __exit__(self, *exc):
        # Terminal invalidation precedes lock acquisition/pruning. The registry
        # holds weak records; ownership never depends on receiving issue's return.
        self.closed = True
        try:
            self.state.abort_joint(self)
        except Exception:
            # A bounded cleanup timeout must not replace an in-flight genuine
            # interruption with a normal DENIED result. Closed remains terminal.
            if (not exc or exc[0] is None) and not _ORDER.terminal():
                raise
        return False


@dataclass(frozen=True)
class _JointRecord:
    handle: object
    lifetime: object
    binding: object
    forecast: object
    forecast_text: str
    label: object
    label_text: str
    forecast_record: object
    label_record: object
    forecast_principal: object
    label_principal: object


class _ReadState:
    def __init__(self, provider, operator, wall, elapsed, clock_domain=None):
        self.provider = self.pinned = provider
        self.repository = provider.repository
        self.reader = self.repository.reader
        self.scope = self.repository.scope.canonical_json
        self.operator = operator
        self.wall, self.elapsed = wall, elapsed
        self.clock_domain = clock_domain
        self.joint_issuers, self.joint_records = {}, WeakValueDictionary()
        self.lock = RLock()
        self.principals, self.grants, self.revoked = {}, {}, set()
        self.last = None

    def clocks(self, cancelled):
        _HOST_HEALTH.require()
        # ALL supported callbacks before final private-record and owner checks.
        stop = cancelled(); elapsed = self.elapsed(); now = self.wall()
        if (stop is not False or any(type(v) not in (int, float) or not math.isfinite(v)
                                   for v in (now, elapsed))):
            raise ForecastReadDenied('read unavailable')
        if self.clock_domain is not None:
            observed = now, elapsed
            now, elapsed = HostReadClock.sample(self.clock_domain)
            if now < observed[0] or elapsed < observed[1]:
                raise ForecastReadDenied('read unavailable')
        def _under_lock_3():
            if self.last is not None and (now < self.last[0] or elapsed < self.last[1]):
                raise ForecastReadDenied('read unavailable')
            self.last = (now, elapsed)
        _locked_call((self.lock,), _under_lock_3)
        return now, elapsed

    def principal(self, handle, now, elapsed):
        p = self.principals.get(id(handle))
        if (not _issued_original(handle) or type(handle) is not HostReadPrincipal or p is None or p.handle is not handle
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
        def _under_lock_4():
            return self.live(handle, text, selection.scope.canonical_json, now, elapsed)
        return _locked_call((self.lock,), _under_lock_4)

    def live(self, handle, text, scope, now, elapsed):
        # Original checks, factored without callbacks; caller owns state lock.
        g = self.grants.get(id(handle))
        if (not _issued_original(handle) or type(handle) is not ExactReadGrant or g is None or g.handle is not handle
                or handle in self.revoked or g.selection_json != text
                or not now < g.expires or not elapsed < g.end):
            raise ForecastReadDenied('read unavailable')
        self.principal(g.principal, now, elapsed)
        if scope != self.scope:
            raise ForecastReadDenied('read unavailable')
        return g

    def admit(self, operation, grant, selection, original, checkpoint):
        if checkpoint is not None:
            return self.check_joint(checkpoint, grant, selection, original, _admit=operation)
        domain = self.clock_domain
        def commit_original():
            now, elapsed = (HostReadClock._sample_locked(domain) if domain is not None else self.last)
            live = self.live(grant, selection.canonical_json, selection.scope.canonical_json, now, elapsed)
            if live is not original or operation.origin.source is not self or operation.provider is not self.provider or operation.grant is not grant:
                raise ForecastReadDenied('read unavailable')
            principal = self.principal(live.principal, now, elapsed)
            remaining = min(live.expires-now, live.end-elapsed, principal.expires-now, principal.end-elapsed, 30)
            return operation.admit(live, remaining)
        locks = ((domain._lock,) if domain is not None else ()) + (self.lock,)
        return _locked_call(locks, commit_original)

    def bind_joint(self, operator, peer):
        # Fixed reviewed peer type, not an arbitrary verification callable.
        from src.services.stored_forecast_accuracy_service import _SyntheticLabels
        if (operator is not self.operator or type(peer) is not _SyntheticLabels
                or peer.operator is not operator or peer.scope != self.scope
                or type(self.clock_domain) is not HostReadClock
                or type(peer.clock_domain) is not HostReadClock
                or self.clock_domain._operator is not operator
                or peer.clock_domain._operator is not operator):
            raise ForecastReadDenied('read unavailable')
        issuer = _JointIssuer()
        def _under_lock_5():
            self.joint_issuers[id(issuer)] = _JointBinding(issuer, peer, self.clock_domain, peer.clock_domain)
        _locked_call((self.lock,), _under_lock_5)
        return issuer

    def joint_scope(self, issuer):
        def _under_lock_6():
            b = self.joint_issuers.get(id(issuer))
            if type(issuer) is not _JointIssuer or b is None or b.handle is not issuer:
                raise ForecastReadDenied('read unavailable')
        _locked_call((self.lock,), _under_lock_6)
        return _JointLifetime(self, issuer)

    def abort_joint(self, lifetime):
        if type(lifetime) is not _JointLifetime or lifetime.state is not self:
            raise ForecastReadDenied('read unavailable')
        lifetime.closed = True
        def _under_lock_7():
            r = lifetime.record
            if r is not None and self.joint_records.get(id(r.handle)) is r:
                del self.joint_records[id(r.handle)]
            lifetime.record = None
        _locked_call((self.lock,), _under_lock_7)

    def issue_joint(self, issuer, forecast, forecast_text, label, label_text, *, _lifetime=None):
        if (type(_lifetime) is not _JointLifetime or _lifetime.state is not self
                or _lifetime.issuer is not issuer or not _lifetime.live()
                or _lifetime.record is not None):
            raise ForecastReadDenied('read unavailable')
        def _under_lock_8():
            b = self.joint_issuers.get(id(issuer))
            if type(issuer) is not _JointIssuer or b is None or b.handle is not issuer:
                raise ForecastReadDenied('read unavailable')
            return b
        b = _locked_call((self.lock,), _under_lock_8)
        # Capture original issuance identities, not success from a declaration.
        def _under_lock_9():
            f, l = self.grants.get(id(forecast)), b.peer.grants.get(id(label))
            if (f is None or l is None or f.handle is not forecast or l.handle is not label
                    or f.selection_json != forecast_text or l.selection_json != label_text):
                raise ForecastReadDenied('read unavailable')
            h = JointReadCheckpoint()
            _lifetime.record = _JointRecord(h, ref(_lifetime), b, forecast, forecast_text, label, label_text,
                f, l, self.principals.get(id(f.principal)), b.peer.principals.get(id(l.principal)))
            self.joint_records[id(h)] = _lifetime.record
            _activate_original(h)
            return h
        return _locked_call(tuple(state.lock for state in sorted((self, b.peer), key=id)), _under_lock_9)

    def check_joint(self, checkpoint, forecast, selection, expected=None, *, _admit=None):
        from src.services.stored_forecast_accuracy_service import _SyntheticLabels
        if (not _issued_original(checkpoint) or type(checkpoint) is not JointReadCheckpoint
                or type(selection) is not ExactForecastRead):
            raise ForecastReadDenied('read unavailable')
        text, scope = selection.canonical_json, selection.scope.canonical_json
        def _under_lock_10():
            r = self.joint_records.get(id(checkpoint))
            if r is None or r.handle is not checkpoint:
                raise ForecastReadDenied('read unavailable')
            return r
        r = _locked_call((self.lock,), _under_lock_10)
        lifetime = r.lifetime()
        if lifetime is None or not lifetime.live() or lifetime.record is not r:
            def _under_lock_12():
                if self.joint_records.get(id(checkpoint)) is r:
                    del self.joint_records[id(checkpoint)]
            _locked_call((self.lock,), _under_lock_12)
            if lifetime is not None:
                self.abort_joint(lifetime)
            raise ForecastReadDenied('read unavailable')
        b = r.binding
        # Global order: distinct clock locks by id, then distinct state locks by
        # id. No callbacks, SQL, supplied samplers or nested inverse lock order.
        def _under_lock_11():
            if (not lifetime.live() or lifetime.record is not r
                    or self.joint_records.get(id(checkpoint)) is not r
                    or self.joint_issuers.get(id(b.handle)) is not b
                    or self.clock_domain is not b.forecast_domain or b.peer.clock_domain is not b.label_domain
                    or type(b.peer) is not _SyntheticLabels or b.peer.operator is not self.operator
                    or forecast is not r.forecast or text != r.forecast_text or scope != b.peer.scope):
                raise ForecastReadDenied('read unavailable')
            samples = {d: HostReadClock._sample_locked(d) for d in sorted({b.forecast_domain, b.label_domain}, key=id)}
            f = _ReadState.live(self, forecast, text, scope, *samples[b.forecast_domain])
            l = _SyntheticLabels.require(b.peer, r.label, r.label_text, *samples[b.label_domain])
            if (f is not r.forecast_record or l is not r.label_record
                    or self.principals.get(id(f.principal)) is not r.forecast_principal
                    or b.peer.principals.get(id(l.principal)) is not r.label_principal
                    or expected is not None and f is not expected):
                raise ForecastReadDenied('read unavailable')
            if _admit is not None:
                if (_admit.origin.source is not self or _admit.provider is not self.provider or _admit.grant is not forecast
                        or _admit.selection.canonical_json != text):
                    raise ForecastReadDenied('original operation required')
                fn, fe = samples[b.forecast_domain]
                ln, le = samples[b.label_domain]
                fp = self.principal(f.principal, fn, fe)
                lp = b.peer.principal(l.principal, ln, le)
                _admit.peer_grant = r.label
                remaining = min(f.expires-fn, f.end-fe, fp.expires-fn, fp.end-fe,
                                l.expires-ln, l.end-le, lp.expires-ln, lp.end-le, 30)
                return _admit.admit((f, l), remaining)
            return r
        return _locked_call(tuple(domain._lock for domain in sorted({b.forecast_domain, b.label_domain}, key=id)) + tuple(state.lock for state in sorted((self, b.peer), key=id)), _under_lock_11)

    def close_joint(self, checkpoint):
        # Invalidation is not contingent on acquiring the pruning lock. Only an
        # exact original registry entry can identify its own lifetime here.
        retained = self.joint_records.get(id(checkpoint))
        if retained is not None and retained.handle is checkpoint:
            lifetime = retained.lifetime()
            if lifetime is not None:
                lifetime.closed = True
        def _under_lock_13():
            r = self.joint_records.get(id(checkpoint))
            if r is not None and r.handle is checkpoint:
                lifetime = r.lifetime()
                if lifetime is not None:
                    lifetime.closed = True
                    lifetime.record = None
                del self.joint_records[id(checkpoint)]
        _locked_call((self.lock,), _under_lock_13)


class ReadAuthority:
    """Host-only composition. Grants explicitly permit full lineage/receipt disclosure."""
    def __init__(self, state):
        self._state = state

    @property
    def host_health(self):
        return _HOST_HEALTH.status()

    def establish_principal(self, operator, *, subject_reference, scope, lifetime_seconds=60):
        s = self._state
        if (operator is not s.operator or type(subject_reference) is not str
                or not 1 <= len(subject_reference) <= 256 or type(scope) is not ForecastStorageScope
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60
                or scope.canonical_json != s.scope):
            raise ForecastReadDenied('read unavailable')
        now, elapsed = s.clocks(lambda: False)
        h = HostReadPrincipal()
        def _under_lock_14():
            s.principals[id(h)] = _Principal(h, subject_reference, scope.canonical_json,
                                           now+lifetime_seconds, elapsed+lifetime_seconds)
        _locked_call((s.lock,), _under_lock_14)
        return _activate_original(h)

    def authorize(self, operator, principal, selection, *, lifetime_seconds=30):
        s = self._state
        if (operator is not s.operator or type(selection) is not ExactForecastRead
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60):
            raise ForecastReadDenied('read unavailable')
        text = ExactForecastRead.model_validate_json(selection.canonical_json).canonical_json
        now, elapsed = s.clocks(lambda: False)
        def _under_lock_15():
            p = s.principal(principal, now, elapsed)
            if selection.scope.canonical_json != p.scope_json:
                raise ForecastReadDenied('read unavailable')
            h = ExactReadGrant()
            s.grants[id(h)] = _Grant(h, principal, text, min(p.expires, now+lifetime_seconds),
                                    min(p.end, elapsed+lifetime_seconds))
            return h
        return _activate_original(_locked_call((s.lock,), _under_lock_15))

    def revoke(self, operator, handle):
        s = self._state
        if operator is not s.operator:
            raise ForecastReadDenied('read unavailable')
        def _under_lock_16():
            record = s.principals.get(id(handle)) or s.grants.get(id(handle))
            if record is None or record.handle is not handle:
                raise ForecastReadDenied('read unavailable')
            s.revoked.add(handle)
        _locked_call((s.lock,), _under_lock_16)


class CanonicalForecastReadService:
    def __init__(self, state):
        self._state = state

    def read(self, grant, selection, *, cancelled=lambda: False, _joint_checkpoint=None):
        try:
            if type(self._state) is not _ReadState:
                raise ForecastReadDenied('original reader required')
            with _operation(self._state._operation_origin, selection, grant) as operation:
                return self._read_owned(operation, grant, selection, cancelled, _joint_checkpoint)
        except Exception:
            return CanonicalReadOutcome(status='DENIED')

    def _read_owned(self, operation, grant, selection, cancelled, _joint_checkpoint):
        s = self._state
        try:
            original = s.require(grant, selection, cancelled)
            if _joint_checkpoint is not None:
                s.check_joint(_joint_checkpoint, grant, selection, original)
            claim = s.admit(operation, grant, selection, original, _joint_checkpoint)
        except Exception:
            return CanonicalReadOutcome(status='DENIED')
        status, records = 'UNAVAILABLE', None
        try:
            # Use retained immutable authorization, never a mutable caller alias.
            approved = ExactForecastRead.model_validate_json(original.selection_json)
            found = s.provider.lookup_claimed(approved.idempotency_id, claim)
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
        except OrderDenied:
            return CanonicalReadOutcome(status='DENIED')
        except Exception:
            status, records = 'UNAVAILABLE', None
        # No content, NOT_FOUND or storage diagnostic escapes after final revocation.
        try:
            if s.require(grant, selection, cancelled) is not original:
                raise ForecastReadDenied('read unavailable')
            if _joint_checkpoint is not None:
                s.check_joint(_joint_checkpoint, grant, selection, original)
        except Exception:
            return CanonicalReadOutcome(status='DENIED')
        try:
            _HOST_HEALTH.require()
        except ForecastReadDenied:
            return CanonicalReadOutcome(status='DENIED')
        outcome = CanonicalReadOutcome(status=status, records=records)
        if status == 'VERIFIED_RECORD_SET':
            return operation.publish(outcome)
        return outcome


def build_canonical_forecast_reader(reader, scope, *, operator, wall_clock=time, elapsed_clock=monotonic, clock_domain=None):
    """Trusted host bootstrap only; return authority separately from consumer service.

    Caller data must never select this factory, credential, operator or principal
    scope. Synthetic host contexts in tests are not production authentication.
    """
    _HOST_HEALTH.require()
    if type(operator) is not object or not callable(wall_clock) or not callable(elapsed_clock):
        raise ForecastReadDenied('read unavailable')
    if clock_domain is not None:
        if type(clock_domain) is not HostReadClock or clock_domain._operator is not operator:
            raise ForecastReadDenied('read unavailable')
        if wall_clock is time: wall_clock = clock_domain.wall
        if elapsed_clock is monotonic: elapsed_clock = clock_domain.elapsed
    factory = ForecastReadConnectionFactory(reader)
    repository = ForecastReadRepository(factory, scope)
    state = _ReadState(ForecastReadProvider(repository), operator, wall_clock, elapsed_clock, clock_domain)
    state._operation_origin = _register_origin(_BOOTSTRAP_CREDENTIAL, 'READ', state.provider, state, None, CanonicalForecastReadService.read.__code__)
    return ReadAuthority(state), CanonicalForecastReadService(state)
