"""Deny-only R7-03 inspection boundary. No executable model-use authority exists.

The host retains the operator; callers retain only exact request-scoped handles.
Object identity is process-local, not independent authentication or a sandbox.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from threading import Lock, RLock
from time import monotonic
from src.data.contracts.runtime_model_use import (
    RuntimeModelUseRequest, RuntimeInspectionGrant, RuntimeModelUseDecision)
from src.services.runtime_model_use_evidence import RuntimeModelUseEvidence


class RuntimeModelUseError(ValueError): pass


def _reject():
    error=RuntimeModelUseError('runtime readiness authority unavailable or invalid')
    try: raise error from None
    finally: error.__cause__=None; error.__context__=None


class RuntimeInspectionCapability:
    __slots__=()
    def __reduce__(self): raise TypeError('process-local capability cannot be serialized')


@dataclass(frozen=True)
class _Grant:
    handle: object
    receipt: RuntimeInspectionGrant
    revoked: bool = False


class _Interrupted(Exception): pass


def _request(value):
    if type(value) is not RuntimeModelUseRequest: raise ValueError()
    request=RuntimeModelUseRequest.model_validate_json(value.canonical_json)
    if len(request.canonical_bytes)>request.limits.max_request_bytes: raise ValueError()
    return request


class _Authority:
    def __init__(self,provider,context,clock,elapsed):
        # Validate the context with the accepted bounded digest contract.
        from src.data.contracts.candidate_lifecycle import AuthorityContext
        self.context=AuthorityContext(authority_id='r7_03_host_inspection',bootstrap_context=context).bootstrap_context
        if type(provider) is not RuntimeModelUseEvidence or provider._self is not provider or not callable(clock) or not callable(elapsed):
            raise ValueError()
        self.provider=provider; self.clock=clock; self.elapsed=elapsed
        self.lock=RLock(); self.operation=Lock(); self.records={}; self.last_time=None
        self.operator=None; self.service=None

    def now(self):
        now=self.clock()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None: raise ValueError()
        now=now.astimezone(timezone.utc)
        if self.last_time is not None and now<self.last_time: raise ValueError()
        self.last_time=now
        return now

    def require(self,handle,request):
        with self.lock:
            record=self.records.get(id(handle)); now=self.now()
            if (type(handle) is not RuntimeInspectionCapability or record is None or record.handle is not handle
                    or record.revoked or request.host_context_id != self.context
                    or record.receipt.request_id != request.request_id
                    or record.receipt.host_context_id != request.host_context_id
                    or record.receipt.actor_id != request.actor_id
                    or not record.receipt.valid_from<=now<record.receipt.expires_at): raise ValueError()
            return record.receipt


class RuntimeModelUseOperator:
    """Host-only inspection issuer, never production/model-execution authority."""
    def __init__(self,root): self._root=root; self._self=self

    def grant_inspection(self,request,*,valid_from,expires_at):
        try:
            root=self._root
            if self._self is not self or root.operator is not self: raise ValueError()
            req=_request(request)
            with root.lock:
                if req.host_context_id!=root.context or len(root.records)>=128: raise ValueError()
                receipt=RuntimeInspectionGrant(request_id=req.request_id,host_context_id=root.context,actor_id=req.actor_id,
                    valid_from=valid_from,expires_at=expires_at)
                if not receipt.valid_from<=root.now()<receipt.expires_at: raise ValueError()
                handle=RuntimeInspectionCapability();root.records[id(handle)]=_Grant(handle,receipt)
                return handle,receipt
        except Exception: pass
        _reject()

    def revoke(self,handle):
        try:
            root=self._root
            if self._self is not self or root.operator is not self: raise ValueError()
            with root.lock:
                r=root.records.get(id(handle))
                if type(handle) is not RuntimeInspectionCapability or r is None or r.handle is not handle: raise ValueError()
                root.records[id(handle)]=replace(r,revoked=True)
                return
        except Exception: pass
        _reject()


class RuntimeModelUseService:
    def __init__(self,root): self._root=root; self._self=self

    def decide(self,request,*,capability=None,cancelled=lambda:False):
        denial=lambda reason:RuntimeModelUseDecision(status='DENIED',reason=reason)
        root=self._root
        if self._self is not self or root.service is not self: return denial('INVALID_AUTHORITY_OR_EVIDENCE')
        if not root.operation.acquire(blocking=False): return denial('BUSY')
        try:
            req=_request(request)
            if not callable(cancelled): raise ValueError()
            previous=None
            def tick():
                nonlocal previous
                value=root.elapsed()
                if type(value) not in (int,float) or not math.isfinite(value) or previous is not None and value<previous:
                    raise ValueError()
                previous=value;return value
            started=tick()
            def interruption():
                return cancelled() is not False or tick()-started>=req.limits.max_elapsed_seconds
            def check():
                if interruption(): raise _Interrupted()
                root.require(capability,req)
                if interruption(): raise _Interrupted()
            check()
            grant=root.require(capability,req)
            first=root.provider.verify(req,check,interruption);check()
            final=root.provider.verify(req,check,interruption);check()
            if first!=final: raise ValueError()
            result=RuntimeModelUseDecision(status='UNAVAILABLE',reason='NATIVE_PAYLOAD_INPUT_AND_MODEL_USE_AUTHORITY_UNAVAILABLE',
                request_id=req.request_id,candidate_id=req.candidate_id,package_id=req.package_id,snapshot_id=req.snapshot_id,
                family=req.family,inspection_grant_id=grant.grant_id,verified_evidence_id=final)
            if len(result.canonical_bytes)>req.limits.max_output_bytes: raise ValueError()
            check()
            return result
        except _Interrupted: reason='CANCELLED_OR_EXPIRED'
        except Exception: reason='INVALID_AUTHORITY_OR_EVIDENCE'
        finally: root.operation.release()
        return denial(reason)


def bootstrap_runtime_model_use(provider,*,host_context_id,clock=lambda:datetime.now(timezone.utc),elapsed_clock=monotonic):
    """Protected startup composition only. Does not fabricate M9 approval."""
    try:
        root=_Authority(provider,host_context_id,clock,elapsed_clock)
        operator=RuntimeModelUseOperator(root);service=RuntimeModelUseService(root)
        root.operator=operator;root.service=service
        return operator,service
    except Exception: pass
    _reject()
