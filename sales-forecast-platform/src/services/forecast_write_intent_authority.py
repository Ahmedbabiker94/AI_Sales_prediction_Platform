"""Independent host-pinned producer-to-write intent. No writer or database.

Protected source, issuer instances, private records and clocks belong to the
trusted host. This is not a Python sandbox, IAM or an atomic revocation lease.
"""
from dataclasses import dataclass, replace
import math
import struct
from threading import Lock
from time import monotonic, time
from uuid import uuid4

from src.data.contracts.backtesting import identity
from src.data.contracts.controlled_h1_execution import ControlledH1ExecutionRequest
from src.data.contracts.b3_forecast_write_intent import (
    B3ForecastRecordSet, B3ForecastRow, ForecastWriteCapability, ForecastWriteGrantDeclaration,
    ForecastWriteIntentLimits, ValidatedForecastWriteIntent, forecast_logical_key, forecast_row_set_identity,
)
from src.data.contracts.successful_b3_producer import ProducerRow, SuccessfulB3Receipt, SuccessfulB3ReceiptDeclaration
from src.services.successful_b3_producer_service import SuccessfulB3ProducerService, _Issued as _ProducerIssued

_VERIFY = SuccessfulB3ProducerService.verify
_COMPOSITION = SuccessfulB3ProducerService._composition
_EVIDENCE = SuccessfulB3ProducerService._evidence


class ForecastWriteIntentError(ValueError):
    pass


def _reject():
    error = ForecastWriteIntentError('forecast write intent unavailable or denied')
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


@dataclass(frozen=True)
class _WriteGrant:
    capability: object
    receipt: object
    producer_record: object
    reference: object
    native: object
    inputs: object
    request_json: str
    producer_json: str
    records_json: str
    grant_json: str
    cancelled: object
    state: str = 'ISSUED'
    reservation: object = None


class ForecastWriteIntentAuthority:
    def __init__(self, producer, *, operator, limits=None, wall_clock=time, elapsed_clock=monotonic):
        try:
            if (type(producer) is not SuccessfulB3ProducerService or producer._self is not producer
                    or type(operator) is not object or not callable(wall_clock) or not callable(elapsed_clock)
                    or limits is not None and type(limits) is not ForecastWriteIntentLimits):
                raise ValueError()
            _COMPOSITION(producer)
            self._producer = self._pinned_producer = producer
            self._operator = operator
            self._limits = ForecastWriteIntentLimits() if limits is None else ForecastWriteIntentLimits.model_validate_json(limits.canonical_json)
            self._wall, self._elapsed = wall_clock, elapsed_clock
            self._issuer_id = identity(['forecast_write_intent_issuer_v1', uuid4().hex])
            self._operation, self._lock = Lock(), Lock()
            self._grants, self._idempotency = {}, {}
            self._active, self._self, self._last_wall = True, self, None
            return
        except Exception:
            pass
        _reject()

    def _composition(self):
        if (self._self is not self or self._active is not True
                or self._producer is not self._pinned_producer):
            raise ValueError()
        _COMPOSITION(self._producer)

    def _checkpoint(self, cancelled, deadline):
        if not callable(cancelled) or type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError()
        previous = start = None

        def check():
            nonlocal previous, start
            # All user/host callbacks run outside both authority and producer locks.
            stop, elapsed, now = cancelled(), self._elapsed(), self._wall()
            if (stop is not False or type(elapsed) not in (int, float) or type(now) not in (int, float)
                    or not math.isfinite(elapsed) or not math.isfinite(now) or now >= deadline
                    or previous is not None and elapsed < previous):
                raise ValueError()
            if start is None:
                start = elapsed
            if elapsed-start >= self._limits.max_elapsed_seconds:
                raise ValueError()
            previous = elapsed
            with self._lock:
                self._composition()
                if self._last_wall is not None and now < self._last_wall:
                    raise ValueError()
                self._last_wall = now
            return float(now)
        return check

    def _verified(self, receipt, reference, native, inputs, request, deadline, check):
        # Original public verifier, never an instance override or supplied verifier.
        def interrupted():
            check()
            return False
        d = _VERIFY(self._producer, receipt, reference, native, inputs, request,
                    deadline=deadline, cancelled=interrupted)
        return SuccessfulB3ReceiptDeclaration.model_validate_json(d.canonical_json)

    def _live_producer(self, receipt, reference, native, inputs, request, producer_json, now, original=None):
        # Caller holds authority lock. No callbacks below; reuse B1's callback-free
        # grant/owner seam after its ORIGINAL public verification and final clocks.
        p = self._producer
        with p._records_lock:
            _COMPOSITION(p)
            r = p._issued.get(id(receipt))
            d = SuccessfulB3ReceiptDeclaration.model_validate_json(producer_json)
            if (type(r) is not _ProducerIssued or r.handle is not receipt or r.active is not True
                    or original is not None and r is not original
                    or r.reference is not reference or r.native is not native or r.inputs is not inputs
                    or r.declaration_json != producer_json or d.producer_id != p._producer_id
                    or d.execution.request != request or not d.issued_at <= now < d.expires_at
                    or r.outcome.result is not r.result or r.outcome.canonical_json != r.outcome_json
                    or r.result.canonical_json != r.result_json or d.execution.canonical_json != r.result_json):
                raise ValueError()
            evidence, _ = _EVIDENCE(p, reference, native, inputs, request, r.capability,
                r.result, now, issuing=False, grant_record=r.grant_record)
            rows = tuple(ProducerRow(scope=grid[0], float32_le=struct.pack('<f', v).hex())
                         for grid, v in zip(evidence.output.representation.samples, r.result.predictions))
            if evidence.record is not r.owner_record or rows != d.rows:
                raise ValueError()
            return r

    def issue(self, operator, receipt, reference, native, inputs, request, *,
              idempotency_id, deadline, lifetime_seconds=30, cancelled=lambda: False):
        """Host-only independent issuance AFTER live original-producer verification."""
        if self._self is not self or not self._operation.acquire(blocking=False):
            _reject()
        try:
            if (operator is not self._operator or type(receipt) is not SuccessfulB3Receipt
                    or type(request) is not ControlledH1ExecutionRequest or type(idempotency_id) is not str
                    or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60):
                raise ValueError()
            req_json = request.canonical_json
            req = ControlledH1ExecutionRequest.model_validate_json(req_json)
            check = self._checkpoint(cancelled, deadline)
            check()
            d = self._verified(receipt, reference, native, inputs, req, deadline, check)
            producer_json = d.canonical_json
            with self._lock:
                entry = self._live_producer(receipt, reference, native, inputs, req, producer_json,
                                           max(self._last_wall, self._producer._last_wall))
            rows = tuple(B3ForecastRow(scope=r.scope, float32_le=r.float32_le,
                         logical_key_id=forecast_logical_key(d.execution, r.scope)) for r in d.rows)
            records = B3ForecastRecordSet(producer=d, idempotency_id=idempotency_id, rows=rows,
                shape=d.shape, row_set_sha256=forecast_row_set_identity(d, rows))
            encoded = records.canonical_json
            if len(encoded.encode()) > self._limits.max_record_bytes:
                raise ValueError()
            check()
            terminal = self._verified(receipt, reference, native, inputs, req, deadline, check)
            # LAST callbacks ran inside the original producer verifier above.
            # Read the current retained record and owner AFTER every callback.
            with self._lock:
                self._composition()
                now = max(self._last_wall, self._producer._last_wall)
                self._live_producer(receipt, reference, native, inputs, req, producer_json, now, entry)
                if (request.canonical_json != req_json or terminal.canonical_json != producer_json
                        or len(self._grants) >= self._limits.max_grants or idempotency_id in self._idempotency):
                    raise ValueError()
                expires = min(now+lifetime_seconds, d.expires_at)
                declaration = ForecastWriteGrantDeclaration(issuer_id=self._issuer_id, ordinal=len(self._grants)+1,
                    producer_receipt_id=d.receipt_id, record_set_id=records.record_set_id,
                    row_set_sha256=records.row_set_sha256, idempotency_id=idempotency_id,
                    issued_at=float(now), expires_at=float(expires), write_deadline=float(min(expires, deadline)))
                # Validate the complete immutable declaration before retaining anything.
                ValidatedForecastWriteIntent(state='ISSUED', grant=declaration, records=records)
                handle = ForecastWriteCapability()
                self._grants[id(handle)] = _WriteGrant(handle, receipt, entry, reference, native, inputs,
                    req_json, producer_json, encoded, declaration.canonical_json, cancelled)
                self._idempotency[idempotency_id] = records.record_set_id
                return handle
        except Exception:
            pass
        finally:
            self._operation.release()
        _reject()

    def _use(self, capability, *, deadline, cancelled, consume):
        if self._self is not self or not self._operation.acquire(blocking=False):
            _reject()
        try:
            if type(capability) is not ForecastWriteCapability:
                raise ValueError()
            bound_cancel = None
            def interrupted():
                # The issuance host's original cancellation source cannot be
                # replaced by a later caller passing the default False poll.
                stop = cancelled()
                host_stop = bound_cancel() if bound_cancel is not None else False
                return False if stop is False and host_stop is False else True
            check = self._checkpoint(interrupted, deadline)
            check()
            with self._lock:
                g = self._grants.get(id(capability))
                if type(g) is not _WriteGrant or g.capability is not capability or g.state != 'ISSUED':
                    raise ValueError()
                bound_cancel = g.cancelled
            declaration = ForecastWriteGrantDeclaration.model_validate_json(g.grant_json)
            records = B3ForecastRecordSet.model_validate_json(g.records_json)
            req = ControlledH1ExecutionRequest.model_validate_json(g.request_json)
            effective = min(deadline, declaration.write_deadline, declaration.expires_at)
            check()
            d = self._verified(g.receipt, g.reference, g.native, g.inputs, req, effective, check)
            with self._lock:
                self._require(g, records, declaration, d, req)
                if consume:
                    # Reservation consumes the attempt PERMANENTLY, before later
                    # checks/materialization. There is no restoration or retry.
                    g = replace(g, state='CONSUMED', reservation=object())
                    self._grants[id(capability)] = g
            check()
            terminal = self._verified(g.receipt, g.reference, g.native, g.inputs, req, effective, check)
            with self._lock:
                self._require(g, records, declaration, terminal, req)
                return ValidatedForecastWriteIntent(state='CONSUMED_NO_WRITE' if consume else 'ISSUED',
                                                     grant=declaration, records=records)
        except Exception:
            pass
        finally:
            self._operation.release()
        _reject()

    def _require(self, g, records, declaration, d, request):
        # No host callbacks. Always validate current original record identities.
        self._composition()
        now = max(self._last_wall, self._producer._last_wall)
        if (self._grants.get(id(g.capability)) is not g or g.state not in ('ISSUED', 'CONSUMED')
                or (g.state == 'CONSUMED') != (g.reservation is not None)
                or declaration.issuer_id != self._issuer_id
                or self._idempotency.get(declaration.idempotency_id) != records.record_set_id
                or not declaration.issued_at <= now < min(declaration.expires_at, declaration.write_deadline)
                or d.canonical_json != g.producer_json or records.producer.canonical_json != g.producer_json
                or records.canonical_json != g.records_json or declaration.canonical_json != g.grant_json
                or g.inputs.binding != request.inputs or request.canonical_json != g.request_json):
            raise ValueError()
        ValidatedForecastWriteIntent(state='ISSUED', grant=declaration, records=records)
        self._live_producer(g.receipt, g.reference, g.native, g.inputs, request,
                            g.producer_json, now, g.producer_record)

    def verify(self, capability, *, deadline, cancelled=lambda: False):
        """Repeatable live inspection of an unreserved original grant, no writes."""
        return self._use(capability, deadline=deadline, cancelled=cancelled, consume=False)

    def simulate_consume(self, capability, *, deadline, cancelled=lambda: False):
        """One-shot reservation/validation ONLY. No transaction or persistence."""
        return self._use(capability, deadline=deadline, cancelled=cancelled, consume=True)

    def revoke(self, operator, capability):
        if self._self is not self or operator is not self._operator:
            _reject()
        with self._lock:
            g = self._grants.get(id(capability))
            if type(g) is not _WriteGrant or g.capability is not capability:
                return False
            self._grants[id(capability)] = replace(g, state='REVOKED')
            return True

    def invalidate(self, operator):
        if self._self is not self or operator is not self._operator:
            _reject()
        with self._lock:
            self._active = False
