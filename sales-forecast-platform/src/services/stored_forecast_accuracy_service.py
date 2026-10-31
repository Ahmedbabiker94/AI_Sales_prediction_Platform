"""Protected synthetic label authorization + original B5-1 reader, no SQL/models.

Trusted host alone constructs this boundary, pins snapshots and retains authority,
operator, reader and private records. Consumers get only evaluator/original grants.
Not a Python sandbox or production IAM. Sequential observed-clock checks do not
provide atomic revocation leases. No serialized result is authorization.
"""
from src.providers._forecast_read_order import _operation, _ORDER, _activate_original, _issued_original, _register_origin, _BOOTSTRAP_CREDENTIAL
from dataclasses import dataclass
from decimal import Decimal
import math
import struct
from threading import RLock
from time import time, monotonic
from src.data.contracts.canonical_forecast_read import ExactForecastRead, CanonicalReadOutcome
from src.data.contracts.forecast_evidence_storage import ForecastStorageScope
from src.data.contracts.stored_forecast_accuracy import (
    LabelTarget, SyntheticLabelSnapshot, ExactLabelRead, AccuracyPair, AccuracyEvidence, AccuracyOutcome,
)
from src.services.canonical_forecast_read_service import CanonicalForecastReadService, HostReadClock, _locked_call, _HOST_HEALTH


class LabelReadDenied(ValueError):
    pass


class LabelPrincipal:
    __slots__ = ()
    def __reduce__(self):
        raise TypeError('original label principal only')


class LabelReadGrant:
    __slots__ = ()
    def __reduce__(self):
        raise TypeError('original label grant only')


@dataclass(frozen=True)
class _Principal:
    handle: object
    subject: str
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


class _SyntheticLabels:
    def __init__(self, snapshots, scope, operator, wall, elapsed, clock_domain):
        self.operator, self.scope = operator, scope.canonical_json
        self.wall, self.elapsed, self.last = wall, elapsed, None
        self.clock_domain = clock_domain
        self.lock = RLock()
        self.principals, self.grants, self.revoked = {}, {}, set()
        # Copy/revalidate, retain no caller mutable aliases; never infer a revision.
        self.snapshots = {}
        for snapshot in snapshots:
            if type(snapshot) is not SyntheticLabelSnapshot:
                raise LabelReadDenied('label read denied')
            copy = SyntheticLabelSnapshot.model_validate_json(snapshot.canonical_json)
            if copy.scope.canonical_json != self.scope or copy.revision_id in self.snapshots:
                raise LabelReadDenied('label read denied')
            self.snapshots[copy.revision_id] = copy.canonical_json

    def clocks(self, cancelled):
        _HOST_HEALTH.require()
        stop = cancelled(); elapsed = self.elapsed(); now = self.wall()
        if stop is not False or any(type(v) not in (int, float) or not math.isfinite(v) for v in (now, elapsed)):
            raise LabelReadDenied('label read denied')
        observed = now, elapsed
        now, elapsed = HostReadClock.sample(self.clock_domain)
        if now < observed[0] or elapsed < observed[1]:
            raise LabelReadDenied('label read denied')
        def _under_lock_1():
            if self.last is not None and (now < self.last[0] or elapsed < self.last[1]):
                raise LabelReadDenied('label read denied')
            self.last = now, elapsed
        _locked_call((self.lock,), _under_lock_1)
        return now, elapsed

    def principal(self, handle, now, elapsed):
        p = self.principals.get(id(handle))
        if (not _issued_original(handle) or type(handle) is not LabelPrincipal or p is None or p.handle is not handle
                or handle in self.revoked or p.scope_json != self.scope
                or not now < p.expires or not elapsed < p.end):
            raise LabelReadDenied('label read denied')
        return p

    def require(self, handle, text, now, elapsed):
        # Caller holds lock. No callback executes under this lock.
        g = self.grants.get(id(handle))
        if (not _issued_original(handle) or type(handle) is not LabelReadGrant or g is None or g.handle is not handle
                or handle in self.revoked or g.selection_json != text
                or not now < g.expires or not elapsed < g.end):
            raise LabelReadDenied('label read denied')
        self.principal(g.principal, now, elapsed)
        return g


class ActualLabelAuthority:
    """Host-only, independent label permission. Synthetic principal != authentication."""
    def __init__(self, state):
        self._state = state

    def establish_principal(self, operator, *, subject_reference, scope, lifetime_seconds=60):
        s = self._state
        if (operator is not s.operator or type(subject_reference) is not str
                or not 1 <= len(subject_reference) <= 256 or type(scope) is not ForecastStorageScope
                or scope.canonical_json != s.scope or type(lifetime_seconds) is not int
                or not 1 <= lifetime_seconds <= 60):
            raise LabelReadDenied('label read denied')
        now, elapsed = s.clocks(lambda: False)
        h = LabelPrincipal()
        def _under_lock_2():
            s.principals[id(h)] = _Principal(h, subject_reference, s.scope,
                now+lifetime_seconds, elapsed+lifetime_seconds)
        _locked_call((s.lock,), _under_lock_2)
        return _activate_original(h)

    def authorize(self, operator, principal, selection, *, lifetime_seconds=30):
        s = self._state
        if (operator is not s.operator or type(selection) is not ExactLabelRead
                or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 60):
            raise LabelReadDenied('label read denied')
        checked = ExactLabelRead.model_validate_json(selection.canonical_json)
        now, elapsed = s.clocks(lambda: False)
        def _under_lock_3():
            p = s.principal(principal, now, elapsed)
            if checked.scope.canonical_json != p.scope_json:
                raise LabelReadDenied('label read denied')
            h = LabelReadGrant()
            s.grants[id(h)] = _Grant(h, principal, checked.canonical_json,
                min(p.expires, now+lifetime_seconds), min(p.end, elapsed+lifetime_seconds))
            return h
        return _activate_original(_locked_call((s.lock,), _under_lock_3))

    def revoke(self, operator, handle):
        s = self._state
        if operator is not s.operator:
            raise LabelReadDenied('label read denied')
        def _under_lock_4():
            record = s.principals.get(id(handle)) or s.grants.get(id(handle))
            if record is None or record.handle is not handle:
                raise LabelReadDenied('label read denied')
            s.revoked.add(handle)
        _locked_call((s.lock,), _under_lock_4)


class StoredForecastAccuracyService:
    def __init__(self, reader, labels):
        self._reader = self._pinned_reader = reader
        self._reader_state = reader._state
        self._labels = self._pinned_labels = labels
        self._joint_issuer = self._reader_state.bind_joint(labels.operator, labels)
        self._publication_origin = _register_origin(_BOOTSTRAP_CREDENTIAL, 'EVAL', self._reader_state.provider, self, labels, StoredForecastAccuracyService.evaluate.__code__)

    def _require(self, fg, forecast, lg, labels, cancelled, checkpoint):
        if (self._reader is not self._pinned_reader or self._reader._state is not self._reader_state
                or self._labels is not self._pinned_labels or type(forecast) is not ExactForecastRead
                or type(labels) is not ExactLabelRead):
            raise LabelReadDenied('evaluation denied')
        f = ExactForecastRead.model_validate_json(forecast.canonical_json)
        l = ExactLabelRead.model_validate_json(labels.canonical_json)
        s = self._labels
        # Callback phase only. Original reader verification remains mandatory.
        s.clocks(cancelled)
        original_forecast = self._reader_state.require(fg, f, cancelled)
        # Callback-free joint cutoff: fresh protected clocks, both original live
        # records/principals, and pinned reader/issuer state under ordered locks.
        record = self._reader_state.check_joint(checkpoint, fg, f, original_forecast)
        if record.label is not lg or record.label_text != l.canonical_json:
            raise LabelReadDenied('evaluation denied')
        return record.forecast_record, record.label_record, f, l

    def evaluate(self, forecast_grant, forecast_selection, label_grant, label_selection, *, cancelled=lambda: False):
        try:
            if type(forecast_selection) is not ExactForecastRead or type(label_selection) is not ExactLabelRead:
                raise LabelReadDenied('evaluation denied')
            forecast = ExactForecastRead.model_validate_json(forecast_selection.canonical_json)
            selection = ExactLabelRead.model_validate_json(label_selection.canonical_json)
            # Cleanup ownership exists before registration. The scope owns any
            # registered record even if issue_joint's return is interrupted.
            with self._reader_state.joint_scope(self._joint_issuer) as ownership:
                checkpoint = self._reader_state.issue_joint(self._joint_issuer, forecast_grant,
                    forecast.canonical_json, label_grant, selection.canonical_json, _lifetime=ownership)
                result = self._evaluate(forecast_grant, forecast_selection, label_grant,
                                      label_selection, cancelled, checkpoint)
            if result.status == 'VERIFIED_EVALUATION':
                with _operation(self._publication_origin, forecast, forecast_grant, label_grant) as operation:
                    return operation.publish(result)
            _HOST_HEALTH.require()
            return result
        except Exception:
            return AccuracyOutcome(status='DENIED')

    def _evaluate(self, forecast_grant, forecast_selection, label_grant, label_selection, cancelled, checkpoint):
        try:
            original_f, original_l, forecast, selection = self._require(
                forecast_grant, forecast_selection, label_grant, label_selection, cancelled, checkpoint)
        except Exception:
            return AccuracyOutcome(status='DENIED')
        status, evidence = 'UNAVAILABLE', None
        try:
            # Original reader performs its own callbacks/checks, then the joint
            # checkpoint immediately before lookup and again before its return.
            outcome = self._reader.read(forecast_grant, forecast, cancelled=cancelled,
                                        _joint_checkpoint=checkpoint)
            checked = CanonicalReadOutcome.model_validate_json(outcome.canonical_json)
            if checked.status == 'DENIED':
                status = 'DENIED'
            elif checked.status == 'VERIFIED_RECORD_SET':
                status, evidence = self._score(checked.records, forecast, selection)
        except Exception:
            status, evidence = 'UNAVAILABLE', None
        try:
            final_f, final_l, _, _ = self._require(forecast_grant, forecast_selection,
                label_grant, label_selection, cancelled, checkpoint)
            if final_f is not original_f or final_l is not original_l:
                raise LabelReadDenied('evaluation denied')
        except Exception:
            return AccuracyOutcome(status='DENIED')
        # No user callback/clock callable runs after the joint final cutoff.
        return AccuracyOutcome(status=status, evidence=evidence)

    def _score(self, records, forecast, selection):
        text = self._labels.snapshots.get(selection.revision_id)
        if text is None:
            return 'LABELS_NOT_READY', None
        snapshot = SyntheticLabelSnapshot.model_validate_json(text)
        if (snapshot.scope != selection.scope or snapshot.content_sha256 != selection.expected_snapshot_sha256
                or snapshot.interval_start != selection.interval_start or snapshot.interval_end != selection.interval_end):
            return 'UNAVAILABLE', None
        rows = records.stored.batch.rows
        # Full semantic identity, no Store/Dept/date-only or neighboring-date join.
        for row in rows:
            if row.key.target_period != selection.interval_start or row.key.target_period_end != selection.interval_end:
                return 'INCOMPATIBLE_SCOPE', None
        as_of = selection.evaluation_as_of
        if (as_of < snapshot.interval_end or snapshot.published_at > as_of or snapshot.effective_at > as_of
                or any(label.available_at > as_of for label in snapshot.labels)):
            return 'LABELS_NOT_READY', None
        labels = {label.target.series_id: label for label in snapshot.labels}
        if set(labels) != set(selection.scope.series_ids):
            return 'LABELS_NOT_READY', None
        pairs, errors = [], []
        for ordinal, row in enumerate(rows):
            actual = labels[row.key.series_id]
            if actual.target != LabelTarget.from_forecast(row.key):
                return 'INCOMPATIBLE_SCOPE', None
            predicted = struct.unpack('<f', bytes.fromhex(row.float32_le))[0]
            error = predicted - float(Decimal(actual.decimal_value))
            if not math.isfinite(error):
                raise ValueError('nonfinite evaluation arithmetic')
            errors.append(error)
            pairs.append(AccuracyPair(ordinal=ordinal, logical_key_id=row.key.logical_key_id,
                forecast_row_sha256=row.row_sha256, prediction_float32_le=row.float32_le, label=actual))
        n = len(errors)
        # Scaled binary64 sums avoid intermediate square overflow/underflow.
        # No clipping or percentage metric for a zero denominator.
        scale = max(abs(e) for e in errors)
        result = AccuracyEvidence(forecast_selection_json=forecast.canonical_json,
            forecast_receipt_json=records.stored.receipt.canonical_json,
            forecast_batch_json=records.stored.batch.canonical_json,
            label_selection=selection, label_snapshot_sha256=snapshot.content_sha256, label_snapshot_json=snapshot.canonical_json,
            pairs=tuple(pairs), population_size=n, matched_count=n,
            mae=math.fsum(abs(e)/n for e in errors),
            rmse=(scale*math.sqrt(math.fsum((e/scale)**2 for e in errors)/n) if scale else 0.),
            bias=math.fsum(e/n for e in errors))
        return 'VERIFIED_EVALUATION', result


def build_stored_forecast_accuracy(reader, scope, snapshots, *, operator, wall_clock=time, elapsed_clock=monotonic, clock_domain=None):
    """Protected host bootstrap only. Synthetic source has NO connection factory.

    Host must authenticate/authorize both principals independently, pin exact label
    revisions/availability metadata, protect original reader/private state, and
    keep the returned authority separate from consumer-facing evaluator/grants.
    """
    _HOST_HEALTH.require()
    if (type(reader) is not CanonicalForecastReadService or type(scope) is not ForecastStorageScope
            or type(operator) is not object or type(snapshots) is not tuple or len(snapshots) > 32
            or not callable(wall_clock) or not callable(elapsed_clock)
            or scope.canonical_json != reader._state.scope):
        raise LabelReadDenied('evaluation denied')
    if clock_domain is None:
        clock_domain = reader._state.clock_domain
    if (type(clock_domain) is not HostReadClock or clock_domain._operator is not operator
            or reader._state.operator is not operator):
        raise LabelReadDenied('protected joint clock provisioning required')
    if wall_clock is time: wall_clock = clock_domain.wall
    if elapsed_clock is monotonic: elapsed_clock = clock_domain.elapsed
    state = _SyntheticLabels(snapshots, scope, operator, wall_clock, elapsed_clock, clock_domain)
    return ActualLabelAuthority(state), StoredForecastAccuracyService(reader, state)
