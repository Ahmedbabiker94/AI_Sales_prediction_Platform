"""Controlled-offline commit coordinator. No SQL, model code or automatic retry.

Host pins the original admissions, operator and storage port at construction.
A storage port is protected host infrastructure, NOT a caller-selected request.
The only deployed storage implementation remains the accepted PG provider.
"""
from dataclasses import dataclass, field
from threading import Lock
import math
import time
from src.handoff.host_admission import ProtectedHostAdmissions
from src.handoff.protocol import HandoffError
from src.data.contracts.forecast_evidence_storage import StoredForecastBatch
from src.database.offline_forecast_postgres import ForecastStorageConflict, ForecastStorageRejected, ForecastStorageRetryable


class CommitCapability:
    __slots__=()
    def __reduce__(self):raise TypeError('original process-local commit authority only')


@dataclass
class _Attempt:
    handle: object
    admission: object
    original: object
    projection: str
    deadline: float
    elapsed_end: float
    cancelled: object
    state: str='COMMIT_AUTHORIZED'
    result: object=None
    # Original process-local reservation survives interrupted state assignments.
    reservation: object=None
    operation: object=field(default_factory=Lock, repr=False, compare=False)


class AuthorizedForecastPersistence:
    def __init__(self, admissions, storage, *, operator, wall=time.time, elapsed=time.monotonic):
        if type(admissions) is not ProtectedHostAdmissions or type(operator) is not object:
            raise HandoffError('protected host composition required')
        if not callable(storage._append_initial) or not callable(storage._lookup):
            raise HandoffError('protected storage port required')
        self._self=self; self._admissions=self._pinned=admissions; self._storage=storage
        self._append=storage._append_initial;self._lookup=storage._lookup
        self._operator=operator;self._wall=wall;self._elapsed=elapsed
        self._records={};self._claimed=set();self._revoked=set();self._lock=Lock()
        self._last_wall=None;self._last_elapsed=None

    def _clock(self, cancelled, deadline):
        stop=cancelled();elapsed=self._elapsed();now=self._wall()
        if (stop is not False or type(now) not in (float,int) or type(elapsed) not in (float,int)
                or not math.isfinite(now) or not math.isfinite(elapsed) or not now<deadline):
            raise HandoffError('commit cancelled or expired')
        with self._lock:
            if (self._self is not self or self._admissions is not self._pinned
                    or self._last_wall is not None and now<self._last_wall
                    or self._last_elapsed is not None and elapsed<self._last_elapsed):
                raise HandoffError('invalid host state')
            self._last_wall=now;self._last_elapsed=elapsed
        return now,elapsed

    def authorize(self, operator, admission, *, deadline, lifetime_seconds=30, cancelled=lambda:False):
        if operator is not self._operator or type(lifetime_seconds) is not int or not 1<=lifetime_seconds<=60:
            raise HandoffError('independent host commit authorization required')
        now,elapsed=self._clock(cancelled,deadline)
        batch=self._admissions.verify(admission,deadline=deadline,cancelled=cancelled)
        original,live=self._admissions._original(admission)
        # Final callbacks, then live original identity validation (no callback below).
        now,elapsed=self._clock(cancelled,min(deadline,original.expires))
        current,live=self._admissions._original(admission)
        with self._lock:
            if current is not original or live.canonical_json!=batch.canonical_json or admission in self._claimed:
                raise HandoffError('original unclaimed admission required')
            h=CommitCapability();end=min(deadline,original.expires,now+lifetime_seconds)
            self._admissions._claim_commit(admission,self,h)
            self._records[id(h)]=_Attempt(h,admission,original,batch.canonical_json,end,elapsed+end-now,cancelled)
            self._claimed.add(admission)
            return h

    def _record(self, h):
        r=self._records.get(id(h))
        if type(h) is not CommitCapability or r is None or r.handle is not h:raise HandoffError('original commit grant required')
        return r

    def commit(self, h, *, cancelled=lambda:False):
        with self._lock:
            r=self._record(h)
            if r.reservation is not None or r.state!='COMMIT_AUTHORIZED' or r.operation.locked():
                raise HandoffError('commit attempt unavailable')
        # Context-managed mutex unwinds even if a signal interrupts state handling.
        # A simultaneous first caller may wait here, but must recheck the spent
        # reservation before callbacks or submission. No second write is possible.
        with r.operation:
            try:
                with self._lock:
                    if r.reservation is not None or r.state!='COMMIT_AUTHORIZED':
                        raise HandoffError('commit attempt unavailable')
                def stop():
                    return False if cancelled() is False and r.cancelled() is False else True
                now,elapsed=self._clock(stop,r.deadline)
                batch=self._admissions.verify(r.admission,deadline=r.deadline,cancelled=stop)
                now,elapsed=self._clock(stop,r.deadline)
                original,live=self._admissions._original(r.admission)
                self._admissions._require_claim(r.admission,self,h)
                # Consistent lock order; final live checks AFTER all callbacks.
                with self._lock, self._admissions._lock:
                    retained=self._admissions._records.get(id(r.admission))
                    claim=self._admissions._commit_claims.get(r.admission)
                    if retained is not r.original or claim is None or claim[0] is not self or claim[1] is not h:
                        raise HandoffError('foreign original admission claim')
                    final_batch=self._admissions._live(retained)
                    if (self._record(h) is not r or r.state!='COMMIT_AUTHORIZED' or r.reservation is not None
                            or h in self._revoked or original is not r.original or live.canonical_json!=r.projection
                            or final_batch.canonical_json!=r.projection or batch.canonical_json!=r.projection
                            or not elapsed<r.elapsed_end):
                        raise HandoffError('commit attempt unavailable')
                    # Irreversible local authorization cutoff, independent of the
                    # readable state label. No supported callbacks below.
                    r.reservation=object()
                    r.state='COMMIT_ATTEMPT_RESERVED'
                with self._lock:
                    r.state='TRANSACTION_IN_PROGRESS'
                try:
                    saved=self._append(batch)
                except (ForecastStorageConflict,ForecastStorageRejected,ForecastStorageRetryable):
                    with self._lock:
                        r.state='COMMIT_REJECTED'
                    return dict(state=r.state,receipt=None)
                saved=StoredForecastBatch.model_validate_json(saved.canonical_json)
                if saved.batch.canonical_json!=r.projection:raise HandoffError('wrong durable content')
                with self._lock:
                    r.result=saved
                    r.state='COMMIT_CONFIRMED'
                return dict(state=r.state,receipt=saved)
            except BaseException:
                # Includes reservation, dispatch, verification AND confirmation.
                # Never suppress KeyboardInterrupt/SystemExit or return success.
                with self._lock:
                    if r.reservation is not None and r.state not in ('COMMIT_CONFIRMED','COMMIT_REJECTED'):
                        r.state='COMMIT_OUTCOME_UNKNOWN'
                raise

    def reconcile(self, operator, h):
        """Trusted SELECT-only recovery of a spent, inactive ORIGINAL attempt.

        The reservation marker, not a possibly interrupted state assignment,
        proves it is spent. An active writer cannot be reconciled concurrently.
        Missing receipt remains UNKNOWN and never permits another submission.
        No crash-durable authority or cross-process recovery is claimed.
        """
        if operator is not self._operator:raise HandoffError('host operator required')
        with self._lock:
            r=self._record(h)
            if r.reservation is None or r.state=='COMMIT_REJECTED':
                raise HandoffError('no unknown reserved attempt')
            if r.operation.locked():raise HandoffError('commit attempt still active')
            if r.state!='COMMIT_CONFIRMED':r.state='COMMIT_OUTCOME_UNKNOWN'
            from src.data.contracts.forecast_evidence_storage import CanonicalForecastStorageBatch
            batch=CanonicalForecastStorageBatch.model_validate_json(r.projection)
        saved=self._lookup(batch.idempotency_id,expected=batch)
        if saved is None:return dict(state='COMMIT_OUTCOME_UNKNOWN',receipt=None)
        saved=StoredForecastBatch.model_validate_json(saved.canonical_json)
        if saved.batch.canonical_json!=r.projection:raise HandoffError('foreign durable receipt')
        with self._lock:
            r.result=saved
            r.state='COMMIT_CONFIRMED'
        return dict(state=r.state,receipt=saved)

    def status(self,h):
        with self._lock:return self._record(h).state

    def revoke(self, operator, h):
        if operator is not self._operator:raise HandoffError('host operator required')
        with self._lock:self._record(h);self._revoked.add(h)
