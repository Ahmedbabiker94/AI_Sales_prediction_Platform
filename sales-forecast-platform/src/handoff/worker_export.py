"""Protected worker-only delegation over ORIGINAL local B1/B2 issuance.

Construction, callbacks, source and private state are host controlled. This is
not a Python sandbox. No detached consumption result can be submitted. Nothing
here writes forecasts or issues a persistence receipt.
"""
from threading import Lock
from src.data.contracts.b3_forecast_write_intent import (
    B3ForecastRecordSet, B3ForecastRow, forecast_logical_key, forecast_row_set_identity,
)
from src.services.forecast_write_intent_authority import ForecastWriteIntentAuthority
from src.handoff.content import project
from src.handoff.protocol import HandoffError, digest, frame, hex64

_ISSUE = ForecastWriteIntentAuthority.issue
_CONSUME = ForecastWriteIntentAuthority.simulate_consume
_VERIFIED = ForecastWriteIntentAuthority._verified
_REQUIRE = ForecastWriteIntentAuthority._require


class _ProtectedExport:
    def __init__(self, issuer, operator, receipt, reference, native, inputs, request, context, *,
                 idempotency, deadline, cancelled=lambda: False):
        if type(issuer) is not ForecastWriteIntentAuthority or issuer._operator is not operator:
            raise HandoffError('protected original issuer required')
        issuer._composition()
        # Copy context by VALUE; retain original authority objects by IDENTITY.
        frame('HELLO', context, {'idempotency':hex64(idempotency),'deadline':deadline})
        self._issuer, self._operator, self._receipt = issuer, operator, receipt
        self._ref, self._native, self._inputs, self._request = reference, native, inputs, request
        self._context = dict(context)
        self._idempotency, self._deadline, self._cancelled = idempotency, deadline, cancelled
        self._self, self._lock, self.state = self, Lock(), 'WORKER_VERIFIED'
        self._grant = self._consumed = self._record = None

    def _check(self):
        if self._self is not self: raise HandoffError('copied export')
        return self._issuer._checkpoint(self._cancelled, self._deadline)

    def proposal(self):
        with self._lock:
            if self.state != 'WORKER_VERIFIED': raise HandoffError('one proposal only')
            try:
                check = self._check(); check()
                p = _VERIFIED(self._issuer, self._receipt, self._ref, self._native, self._inputs,
                              self._request, self._deadline, check)
                rows = tuple(B3ForecastRow(scope=r.scope, float32_le=r.float32_le,
                    logical_key_id=forecast_logical_key(p.execution,r.scope)) for r in p.rows)
                records = B3ForecastRecordSet(producer=p, idempotency_id=self._idempotency,
                    rows=rows, shape=p.shape, row_set_sha256=forecast_row_set_identity(p,rows))
                self._body = {'records':records.canonical_json,'projection':project(records).canonical_json}
                # Complete ALL frame/content size checks before consuming a grant.
                encoded = frame('PROPOSAL',self._context,self._body)
                self._proposal = digest(self._body)
                self.state = 'PROPOSAL_RECEIVED'
                return encoded
            except Exception:
                self.state = 'REJECTED'
                raise

    def reserve(self, approval):
        with self._lock:
            if self.state != 'PROPOSAL_RECEIVED': raise HandoffError('invalid reservation state')
            try:
                if set(approval) != {'proposal','approval'} or approval['proposal'] != self._proposal:
                    raise HandoffError('unapproved proposal')
                self._approval = hex64(approval['approval'])
                a = self._issuer
                self._grant = _ISSUE(a,self._operator,self._receipt,self._ref,self._native,self._inputs,
                    self._request,idempotency_id=self._idempotency,deadline=self._deadline,
                    lifetime_seconds=60,cancelled=self._cancelled)
                # Only ORIGINAL successful invocation retained. Consumed state alone
                # or an externally submitted CONSUMED_NO_WRITE is insufficient.
                self._consumed = _CONSUME(a,self._grant,deadline=self._deadline,cancelled=self._cancelled)
                if self._consumed.state != 'CONSUMED_NO_WRITE' or self._consumed.records.canonical_json != self._body['records']:
                    raise HandoffError('consumption differs from approval')
                self._record = a._grants[id(self._grant)]
                if self._record.state != 'CONSUMED' or self._record.reservation is None:
                    raise HandoffError('missing original reservation')
                self._reservation = digest({'session':self._context,'grant':self._consumed.grant.grant_id,
                    'proposal':self._proposal,'approval':self._approval})
                self.state = 'GRANT_RESERVED'
                return frame('RESERVED',self._context,{'proposal':self._proposal,'approval':self._approval,
                    'grant':self._consumed.grant.grant_id,'reservation':self._reservation})
            except Exception:
                self.state = 'REJECTED'
                raise

    def release(self, challenge):
        with self._lock:
            if self.state != 'GRANT_RESERVED': raise HandoffError('release unavailable')
            try:
                expected = {'proposal':self._proposal,'approval':self._approval,'reservation':self._reservation}
                if set(challenge) != set(expected)|{'final_challenge'} or any(challenge[k]!=v for k,v in expected.items()):
                    raise HandoffError('foreign final challenge')
                hex64(challenge['final_challenge'])
                a, result, g = self._issuer,self._consumed,self._record
                body = dict(challenge,grant=result.grant.grant_id,
                    release=digest([self._context,challenge,result.grant.grant_id]),
                    expires=min(result.grant.expires_at,result.grant.write_deadline,self._deadline))
                raw = frame('FINAL_RELEASE',self._context,body)
                check = self._check(); check()
                terminal = _VERIFIED(a,self._receipt,self._ref,self._native,self._inputs,self._request,
                                     body['expires'],check)
                # LAST callbacks above. Final original record/owner/producer checks
                # below are callback-free. Sequential cutoff, NOT a revocation lease.
                with a._lock:
                    if (a._grants.get(id(self._grant)) is not g or g.reservation is None
                            or g.state != 'CONSUMED' or result.records.canonical_json != self._body['records']):
                        raise HandoffError('original reservation changed')
                    _REQUIRE(a,g,result.records,result.grant,terminal,self._request)
                    self.state = 'FINAL_RELEASE'
                    return raw
            except Exception:
                self.state = 'REJECTED'
                raise
