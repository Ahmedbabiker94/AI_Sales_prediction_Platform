"""Independent one-shot authority. Protected operator object identity, not IAM.

All host callbacks must complete BEFORE live registry validation. Reservations
are consumed on every subsequent outcome, including failure; no retry restores a
use. Revocation is permanent. Nothing here authorizes production or persistence.
"""
from dataclasses import dataclass
from threading import Lock
from time import time
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest, ExecutionGrantDeclaration
from src.providers.owned_execution_provider import OwnedExecutionProvider


class ExecutionCapability:
    __slots__=()
    def __reduce__(self): raise TypeError('execution capability is process-local')


@dataclass(frozen=True)
class _Grant:
    capability: object
    reference: object
    native: object
    inputs: object
    owner_record: object
    declaration: ExecutionGrantDeclaration
    state: str
    reservation: object = None


class ControlledExecutionAuthority:
    def __init__(self, provider, *, operator, wall_clock=time):
        if type(provider) is not OwnedExecutionProvider or type(operator) is not object or not callable(wall_clock):
            raise ValueError('protected authority composition required')
        self._provider=provider; self._operator=operator; self._clock=wall_clock
        self._lock=Lock(); self._grants={}; self._self=self; self._last_time=None

    def issue(self, operator, reference, native, inputs, request, *, lifetime_seconds=30, execution_seconds=30):
        try:
            if (self._self is not self or operator is not self._operator or type(request) is not ControlledH1ExecutionRequest
                    or type(lifetime_seconds) is not int or type(execution_seconds) is not int
                    or not 1<=execution_seconds<=lifetime_seconds<=60): raise ValueError()
            req=ControlledH1ExecutionRequest.model_validate_json(request.canonical_json)
            now=self._clock()
            if type(now) not in (int,float): raise ValueError()
            declaration=ExecutionGrantDeclaration(request=req,issued_at=float(now),expires_at=float(now+lifetime_seconds),
                execution_deadline=float(now+execution_seconds))
            evidence=self._provider.require(reference,native,inputs,req)
            capability=ExecutionCapability()
            with self._lock:
                if self._last_time is not None and now<self._last_time: raise ValueError()
                self._last_time=now
                if len(self._grants)>=128: raise ValueError()
                self._grants[id(capability)]=_Grant(capability,reference,native,inputs,evidence.record,declaration,'ISSUED')
            return capability
        except Exception: pass
        raise ValueError('execution grant unavailable') from None

    def revoke(self, operator, capability):
        if self._self is not self or operator is not self._operator: raise ValueError('execution grant unavailable')
        with self._lock:
            g=self._grants.get(id(capability))
            if g is None or g.capability is not capability: return False
            self._grants[id(capability)]=_Grant(g.capability,g.reference,g.native,g.inputs,g.owner_record,g.declaration,'REVOKED')
            return True

    def _validate(self, capability, reference, native, inputs, request, now, reservation, reserve):
        # No clocks, cancellation or host callback under this lock.
        if self._self is not self or type(capability) is not ExecutionCapability: raise ValueError()
        with self._lock:
            if self._last_time is not None and now<self._last_time: raise ValueError()
            self._last_time=now
            g=self._grants.get(id(capability))
            expected='ISSUED' if reserve else 'CONSUMED'
            if (g is None or g.capability is not capability or g.state!=expected
                    or g.reference is not reference or g.native is not native or g.inputs is not inputs
                    or g.declaration.request!=request or not g.declaration.issued_at<=now<g.declaration.execution_deadline
                    or not now<g.declaration.expires_at or not reserve and g.reservation is not reservation): raise ValueError()
            evidence=self._provider.require(reference,native,inputs,request)
            if evidence.record is not g.owner_record: raise ValueError()
            if reserve:
                reservation=object()
                self._grants[id(capability)]=_Grant(g.capability,g.reference,g.native,g.inputs,g.owner_record,g.declaration,'CONSUMED',reservation)
            return evidence,g.declaration,reservation
