"""Protected composition over ORIGINAL B3 execution, with no writer or loader.

Host owns the service, operator object, source and clocks. Python reflection or
replacement of private functions/registries is outside this trust boundary.
Verification attests a past successful execution, not permission to execute it
again. All callbacks finish before final live checks; those checks are not leases.
"""
from dataclasses import dataclass, replace
import math
import struct
from threading import Lock
from time import monotonic, time
from uuid import uuid4

from src.data.contracts.backtesting import identity
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest, ExecutionOutcome
from src.data.contracts.successful_b3_producer import (
    ProducerLimits, ProducerRow, SuccessfulB3Receipt, SuccessfulB3ReceiptDeclaration, result_set_identity,
)
from src.providers.owned_execution_provider import OwnedExecutionProvider
from src.services.controlled_execution_authority import ControlledExecutionAuthority
from src.services.controlled_h1_execution_service import ControlledH1ExecutionService

_EXECUTE = ControlledH1ExecutionService.execute


class ProducerEvidenceError(ValueError):
    pass


def _reject():
    error = ProducerEvidenceError('successful producer evidence unavailable or denied')
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


@dataclass(frozen=True)
class _Issued:
    handle: object
    reference: object
    native: object
    inputs: object
    owner_record: object
    capability: object
    grant_record: object
    outcome: object
    result: object
    outcome_json: str
    result_json: str
    declaration_json: str
    active: bool = True


class SuccessfulB3ProducerService:
    def __init__(self, service, *, operator, limits=None, wall_clock=time, elapsed_clock=monotonic):
        try:
            if (type(service) is not ControlledH1ExecutionService or service._self is not service
                    or type(service._authority) is not ControlledExecutionAuthority
                    or service._authority._self is not service._authority
                    or type(service._authority._provider) is not OwnedExecutionProvider
                    or type(operator) is not object or not callable(wall_clock) or not callable(elapsed_clock)
                    or limits is not None and type(limits) is not ProducerLimits):
                raise ValueError()
            self._service = service
            self._authority = service._authority
            self._provider = service._authority._provider
            self._operator = operator
            self._limits = ProducerLimits() if limits is None else ProducerLimits.model_validate_json(limits.canonical_json)
            self._wall = wall_clock
            self._elapsed = elapsed_clock
            self._producer_id = identity(['successful_b3_producer_instance_v1', uuid4().hex])
            self._operation = Lock()
            self._records_lock = Lock()
            self._issued = {}
            self._last_wall = None
            self._active = True
            self._self = self
            return
        except Exception:
            pass
        _reject()

    def _composition(self):
        if (self._self is not self or self._active is not True
                or self._service._self is not self._service or self._service._authority is not self._authority
                or self._authority._self is not self._authority or self._authority._provider is not self._provider
                or self._provider._self is not self._provider):
            raise ValueError()

    def _checkpoint(self, cancelled, deadline):
        if (not callable(cancelled) or type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError()
        previous = start = None

        def check():
            nonlocal previous, start
            # No cancellation/clock callback while holding either live-record lock.
            stop = cancelled()
            elapsed = self._elapsed()
            now = self._wall()
            if (stop is not False or type(elapsed) not in (int, float) or type(now) not in (int, float)
                    or not math.isfinite(elapsed) or not math.isfinite(now) or now >= deadline
                    or previous is not None and elapsed < previous):
                raise ValueError()
            if start is None:
                start = elapsed
            if elapsed-start >= self._limits.max_elapsed_seconds:
                raise ValueError()
            previous = elapsed
            with self._records_lock:
                self._composition()
                if self._last_wall is not None and now < self._last_wall:
                    raise ValueError()
                self._last_wall = now
            return float(now)
        return check

    def _evidence(self, reference, native, inputs, request, capability, result, now, *, issuing, grant_record=None):
        # Caller holds producer record lock. Neither accepted require() nor these
        # live grant/owner reads invokes a host clock or interruption callback.
        with self._authority._lock:
            g = self._authority._grants.get(id(capability))
            if (g is None or g.capability is not capability or g.state != 'CONSUMED'
                    or g.reservation is None or g.reference is not reference or g.native is not native
                    or g.inputs is not inputs or g.declaration.canonical_json != result.grant.canonical_json
                    or g.declaration.request != request or grant_record is not None and g is not grant_record):
                raise ValueError()
            if issuing and not g.declaration.issued_at <= now < min(g.declaration.expires_at, g.declaration.execution_deadline):
                raise ValueError()
            evidence = self._provider.require(reference, native, inputs, request)
            if evidence.record is not g.owner_record:
                raise ValueError()
            return evidence, g

    def produce(self, operator, reference, native, inputs, request, *, capability,
                deadline, lifetime_seconds=30, cancelled=lambda: False):
        """Invoke B3 exactly once. Failures never publish a receipt or retry B3."""
        if self._self is not self or not self._operation.acquire(blocking=False):
            _reject()
        try:
            if (operator is not self._operator or type(request) is not ControlledH1ExecutionRequest
                    or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60):
                raise ValueError()
            req_json = request.canonical_json
            req = ControlledH1ExecutionRequest.model_validate_json(req_json)
            check = self._checkpoint(cancelled, deadline)
            check()
            entry = self._provider.require(reference, native, inputs, req)
            with self._records_lock:
                if len(self._issued) >= self._limits.max_receipts:
                    raise ValueError()
            def interrupted():
                check()
                return False
            # No executor/result injection point. Ignore any instance execute alias.
            outcome = _EXECUTE(self._service, reference, native, inputs, req,
                               capability=capability, cancelled=interrupted)
            if type(outcome) is not ExecutionOutcome or outcome.status != 'EXECUTED' or outcome.result is None:
                raise ValueError()
            outcome_json = outcome.canonical_json
            checked = ExecutionOutcome.model_validate_json(outcome_json)
            original_result = outcome.result
            result_json = original_result.canonical_json
            if checked.result.request != req or checked.result.canonical_json != result_json:
                raise ValueError()
            points = entry.output.representation.samples
            if any(len(grid) != 1 for grid in points):
                raise ValueError()
            rows = tuple(ProducerRow(scope=grid[0], float32_le=struct.pack('<f', v).hex())
                         for grid, v in zip(points, checked.result.predictions))
            now = check()  # LAST supported callbacks, before fresh grant/owner lookup.
            declaration = SuccessfulB3ReceiptDeclaration(producer_id=self._producer_id,
                ordinal=len(self._issued)+1, issued_at=now, expires_at=now+lifetime_seconds,
                execution=checked.result, rows=rows, shape=(len(rows),),
                result_set_sha256=result_set_identity(checked.result, rows))
            encoded = declaration.canonical_json
            if len(encoded.encode()) > self._limits.max_receipt_bytes:
                raise ValueError()
            handle = SuccessfulB3Receipt()
            with self._records_lock:
                self._composition()
                evidence, grant = self._evidence(reference, native, inputs, req, capability, original_result, now, issuing=True)
                if (evidence.record is not entry.record or request.canonical_json != req_json
                        or outcome.result is not original_result or outcome.canonical_json != outcome_json
                        or original_result.canonical_json != result_json):
                    raise ValueError()
                self._issued[id(handle)] = _Issued(handle, reference, native, inputs, evidence.record,
                    capability, grant, outcome, original_result, outcome_json, result_json, encoded)
                return handle
        except Exception:
            pass
        finally:
            self._operation.release()
        _reject()

    def verify(self, receipt, reference, native, inputs, request, *, deadline, cancelled=lambda: False):
        """Repeatable read-only verification. Returns a detached declaration copy."""
        if self._self is not self or not self._operation.acquire(blocking=False):
            _reject()
        try:
            if type(receipt) is not SuccessfulB3Receipt or type(request) is not ControlledH1ExecutionRequest:
                raise ValueError()
            req_json = request.canonical_json
            req = ControlledH1ExecutionRequest.model_validate_json(req_json)
            check = self._checkpoint(cancelled, deadline)
            check()
            with self._records_lock:
                record = self._issued.get(id(receipt))
                if type(record) is not _Issued or record.handle is not receipt:
                    raise ValueError()
            declaration = SuccessfulB3ReceiptDeclaration.model_validate_json(record.declaration_json)
            now = check()  # Nothing below calls host clocks/cancellation or B3.
            with self._records_lock:
                self._composition()
                if (self._issued.get(id(receipt)) is not record or record.active is not True
                        or record.reference is not reference or record.native is not native or record.inputs is not inputs
                        or not declaration.issued_at <= now < declaration.expires_at
                        or declaration.producer_id != self._producer_id or declaration.execution.request != req
                        or request.canonical_json != req_json or record.outcome.result is not record.result
                        or record.outcome.canonical_json != record.outcome_json
                        or record.result.canonical_json != record.result_json
                        or declaration.execution.canonical_json != record.result_json):
                    raise ValueError()
                evidence, _ = self._evidence(reference, native, inputs, req, record.capability,
                    record.result, now, issuing=False, grant_record=record.grant_record)
                if evidence.record is not record.owner_record:
                    raise ValueError()
                rows = tuple(ProducerRow(scope=grid[0], float32_le=struct.pack('<f', v).hex())
                             for grid, v in zip(evidence.output.representation.samples, record.result.predictions))
                if rows != declaration.rows:
                    raise ValueError()
                return declaration
        except Exception:
            pass
        finally:
            self._operation.release()
        _reject()

    def revoke(self, operator, receipt):
        if self._self is not self or operator is not self._operator:
            _reject()
        with self._records_lock:
            record = self._issued.get(id(receipt))
            if type(record) is not _Issued or record.handle is not receipt:
                return False
            self._issued[id(receipt)] = replace(record, active=False)
            return True

    def invalidate(self, operator):
        if self._self is not self or operator is not self._operator:
            _reject()
        with self._records_lock:
            self._active = False
