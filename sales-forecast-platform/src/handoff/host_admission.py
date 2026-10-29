"""Original broker-owned process-local retention. No declarations grant authority.

Trusted host controls construction, source, callbacks, Docker and private state.
Not an adversarial Python sandbox, cryptographic attestation or durable IAM.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import importlib.util
import json
import time
from src.handoff.protocol import HandoffError, canonical
from src.handoff.content import inspect_proposal


class AdmissionHandle:
    __slots__ = ()
    def __reduce__(self): raise TypeError('original process-local admission only')


@dataclass(frozen=True)
class _Admission:
    handle: object
    session: object
    channel: object
    context: str
    release: str
    proposal: str
    projection: str
    expires: float
    end: float


class ProtectedHostAdmissions:
    def __init__(self, *, operator, wall=time.time, elapsed=time.monotonic):
        if type(operator) is not object: raise HandoffError('host operator required')
        self._self=self; self._operator=operator; self._wall=wall; self._elapsed=elapsed
        self._records={}; self._commit_claims={}; self._revoked=set(); self._pending={}; self._lock=Lock()
        project=Path(__file__).resolve().parents[2]
        spec=importlib.util.spec_from_file_location('_original_b4b_host_broker',project/'tests/native/run_native_handoff.py')
        broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
        self._broker=broker;self._run=broker.run;self._session_type=broker._HostAdmission

    def capture(self, operator, *, approve, cancelled=lambda:False, timeout=240):
        if self._self is not self or operator is not self._operator: raise HandoffError('host operator required')
        ticket=object()
        with self._lock:self._pending[ticket]=None
        try:
            # ORIGINAL broker itself invokes _stage; no detached result input API.
            response=self._run(allow_native_handoff=True,use_recovered_image=True,approve=approve,
                cancelled=cancelled,timeout=timeout,_retention=(self,ticket))
            with self._lock:
                record=self._pending.pop(ticket)
                if (record is None or response['state']!='VALIDATED_NO_WRITE'
                        or response['cleanup']!='CLEANUP_SUCCESS' or response['worker_exit']!=0):
                    raise HandoffError('original broker failed')
                self._live(record)
                self._records[id(record.handle)]=record
                return record.handle
        finally:
            with self._lock:self._pending.pop(ticket,None)

    def _stage(self, ticket, session, channel, release):
        # Called ONLY inside the original reviewed broker after original admission.
        with self._lock:
            if (ticket not in self._pending or self._pending[ticket] is not None
                    or type(session) is not self._session_type or session.channel is not channel
                    or session.state!='HOST_ADMITTED_NO_WRITE' or session._self is not session):
                raise HandoffError('original broker admission required')
            records,batch=inspect_proposal(json.loads(session.body))
            if (records.idempotency_id!=session.idempotency or release['proposal']!=session.proposal_id
                    or release['grant']!=session.grant or release['approval']!=session.approval
                    or release['reservation']!=session.reservation or release['final_challenge']!=session.final_challenge):
                raise HandoffError('original release required')
            now=self._wall();elapsed=self._elapsed()
            expires=min(release['expires'],session.deadline,records.producer.expires_at,now+60)
            if now>=expires:raise HandoffError('admission expired')
            handle=AdmissionHandle()
            self._pending[ticket]=_Admission(handle,session,channel,canonical(session.context),canonical(release),
                session.body,batch.canonical_json,expires,elapsed+expires-now)

    def _live(self, record):
        s=record.session
        if (self._self is not self or record.handle in self._revoked or not s.active or s._self is not s
                or s.channel is not record.channel or s.state!='HOST_ADMITTED_NO_WRITE'
                or canonical(s.context)!=record.context or s.body!=record.proposal):
            raise HandoffError('original admission unavailable')
        release=json.loads(record.release)
        if (release['proposal']!=s.proposal_id or release['approval']!=s.approval
                or release['grant']!=s.grant or release['reservation']!=s.reservation
                or release['final_challenge']!=s.final_challenge):
            raise HandoffError('original release changed')
        r,b=inspect_proposal(json.loads(record.proposal))
        if b.canonical_json!=record.projection or r.idempotency_id!=s.idempotency:
            raise HandoffError('admission content changed')
        return b

    def _original(self, handle):
        with self._lock:
            r=self._records.get(id(handle))
            if type(handle) is not AdmissionHandle or r is None or r.handle is not handle:
                raise HandoffError('original admission required')
            return r,self._live(r)

    def _claim_commit(self, handle, issuer, capability):
        with self._lock:
            r=self._records.get(id(handle))
            if r is None or r.handle is not handle or handle in self._commit_claims:
                raise HandoffError('admission already claimed')
            self._live(r)
            self._commit_claims[handle]=(issuer,capability)

    def _require_claim(self, handle, issuer, capability):
        with self._lock:
            claim=self._commit_claims.get(handle)
            if claim is None or claim[0] is not issuer or claim[1] is not capability:
                raise HandoffError('foreign commit issuer')

    def verify(self, handle, *, deadline, cancelled=lambda:False):
        # All callbacks BEFORE final original record and private-state checks.
        stop=cancelled();elapsed=self._elapsed();now=self._wall()
        r,b=self._original(handle)
        if stop is not False or not now<min(deadline,r.expires) or not elapsed<r.end:
            raise HandoffError('admission cancelled or expired')
        return b

    def revoke(self, operator, handle):
        if operator is not self._operator:raise HandoffError('host operator required')
        with self._lock:self._revoked.add(handle)
