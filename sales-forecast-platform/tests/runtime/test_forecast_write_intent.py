"""Write-intent contract tests: mocked B3 origin, real frozen M3 fixture.

These are NOT genuine native-origin proof. The separate native module uses the
original B3 service and independently issued execution grants inside P0 Docker.
"""
import copy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import pickle
import runpy
import sqlite3
import struct
from types import SimpleNamespace
from uuid import UUID

import pytest
from src.data.contracts.b3_forecast_write_intent import (
    B3ForecastRecordSet, B3ForecastRow, ForecastWriteCapability, ForecastWriteGrantDeclaration,
    ForecastWriteIntentLimits, ValidatedForecastWriteIntent, forecast_logical_key, forecast_row_set_identity,
)
from src.data.contracts.canonical_forecast_persistence import CanonicalH1ForecastDeclaration
from src.data.contracts.successful_b3_producer import SuccessfulB3Receipt
from src.data.contracts.successful_b3_producer import ProducerRow, result_set_identity
from src.services.forecast_write_intent_authority import ForecastWriteIntentAuthority, ForecastWriteIntentError
from src.services.successful_b3_producer_service import ProducerEvidenceError

BASE = runpy.run_path('tests/runtime/test_successful_b3_producer.py')


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    generator = BASE['prepared'].__wrapped__(tmp_path_factory)
    result = next(generator)
    try:
        yield result
    finally:
        with pytest.raises(StopIteration): next(generator)


@pytest.fixture
def make(prepared, monkeypatch):
    original = BASE['make'].__wrapped__(prepared, monkeypatch)
    def create(*, produce=True, **kwargs):
        s = original()
        s.receipt = BASE['issue'](s, lifetime_seconds=60) if produce else None
        s.write_operator = object()
        s.write = ForecastWriteIntentAuthority(s.producer, operator=s.write_operator,
            wall_clock=lambda: s.control['wall'], elapsed_clock=lambda: s.control['elapsed'], **kwargs)
        return s
    return create


def issue(s, **kwargs):
    args = dict(idempotency_id='c'*64, deadline=150.)
    args.update(kwargs)
    return s.write.issue(s.write_operator, s.receipt, s.reference, s.native, s.inputs, s.request, **args)


def verify(s, handle, **kwargs):
    args = dict(deadline=200.); args.update(kwargs)
    return s.write.verify(handle, **args)


def consume(s, handle, **kwargs):
    args = dict(deadline=200.); args.update(kwargs)
    return s.write.simulate_consume(handle, **args)


def test_original_producer_pinned_independent_issuance_and_exact_retention(make):
    s = make()
    with pytest.raises(ForecastWriteIntentError): verify(s, s.receipt)
    with pytest.raises(ForecastWriteIntentError): verify(s, s.cap)
    h = issue(s); g = s.write._grants[id(h)]; r = s.producer._issued[id(s.receipt)]
    assert s.write._producer is s.producer and g.producer_record is r and g.receipt is s.receipt
    assert r.result is s.returned[0].result and r.outcome is s.returned[0]
    assert r.reference is g.reference is s.reference and r.native is g.native is s.native
    assert r.inputs is g.inputs is s.inputs and r.capability is s.cap
    a = verify(s, h); b = verify(s, h)
    assert a.canonical_bytes == b.canonical_bytes and a is not b and a.records is not b.records
    assert a.state == 'ISSUED' and a.grant.purpose == 'CONTROLLED_OFFLINE_H1_EVIDENCE_WRITE'
    assert a.records.shape == (4,) and a.grant.idempotency_id == 'c'*64
    assert a.records.producer.execution.canonical_json == r.result_json
    assert not a.writes_performed and not a.persistence_completed and a.durable_receipt is None
    assert a.grant.correction_policy == a.records.correction_policy == 'INITIAL_WRITE_ONLY'
    out = consume(s, h)
    assert out.state == 'CONSUMED_NO_WRITE' and out.records.canonical_bytes == a.records.canonical_bytes
    assert s.write._grants[id(h)].state == 'CONSUMED' and not out.writes_performed
    for fn in (verify, consume):
        with pytest.raises(ForecastWriteIntentError): fn(s, h)


def test_exact_float32_little_endian_bits_signed_zero_and_roundtrip(make):
    s = make(produce=False); s.control['predictions'] = (-0., 0., 1.5, -2.)
    s.receipt = BASE['issue'](s, lifetime_seconds=60)
    v = verify(s, issue(s)); rows = v.records.rows
    assert [r.float32_le for r in rows] == ['00000080', '00000000', '0000c03f', '000000c0']
    raw = b''.join(bytes.fromhex(r.float32_le) for r in rows)
    assert raw == struct.pack('<4f', *s.returned[0].result.predictions)
    assert struct.pack('<4f', *(r.prediction_value for r in rows)) == raw
    parsed = B3ForecastRecordSet.model_validate_json(v.records.canonical_json)
    assert parsed.canonical_bytes == v.records.canonical_bytes
    assert parsed.record_set_id == v.records.record_set_id and parsed.row_set_sha256 == v.records.row_set_sha256
    with pytest.raises(ValueError): parsed.idempotency_id = 'e'*64


def test_logical_keys_exactly_reuse_accepted_r7_05a_property(make):
    s = make(); v = verify(s, issue(s)); e = v.records.producer.execution
    sr = e.request.inputs.request.snapshot_request
    for row in v.records.rows:
        p = row.scope
        legacy = SimpleNamespace(series_id=p.series_id, provenance=SimpleNamespace(transformation='identity_target_v1',
            prediction=SimpleNamespace(inspection=SimpleNamespace(snapshot_request=sr,
                candidate_id=e.request.candidate_id, family=e.request.family), target_period=p.target_period,
                target_period_end=p.target_period_end, horizon_step=1)))
        assert row.logical_key_id == CanonicalH1ForecastDeclaration.logical_key_id.fget(legacy)
    keys = tuple(r.logical_key_id for r in v.records.rows)
    assert len(set(keys)) == 4
    changed_sr = sr.model_copy(update={'capture_cutoff': sr.capture_cutoff-timedelta(days=1),
        'dataset': sr.dataset.model_copy(update={'admission_digest': 'f'*64})})
    changed = e.model_copy(update={'request': e.request.model_copy(update={'inputs': e.request.inputs.model_copy(update={
        'request': e.request.inputs.request.model_copy(update={'snapshot_request': changed_sr})})})})
    assert tuple(forecast_logical_key(changed, r.scope) for r in v.records.rows) == keys
    for field in ('tenant', 'dataset', 'candidate', 'family'):
        req = e.request
        if field in ('candidate', 'family'):
            req = req.model_copy(update={('candidate_id' if field == 'candidate' else field): ('f'*64 if field == 'candidate' else 'lightgbm')})
        else:
            ref = replace(sr.dataset.reference, **{('organization_id' if field == 'tenant' else 'dataset_id'): UUID(int=999)})
            other = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'reference': ref})})
            req = req.model_copy(update={'inputs': req.inputs.model_copy(update={'request': req.inputs.request.model_copy(update={'snapshot_request': other})})})
        assert forecast_logical_key(e.model_copy(update={'request': req}), v.records.rows[0].scope) != keys[0]


@pytest.mark.parametrize('fault', ['detached', 'new_handle', 'execution_cap', 'result', 'foreign', 'copy', 'pickle'])
def test_receipt_not_values_or_foreign_issuer_establishes_origin(make, fault):
    s = make()
    if fault in ('copy', 'pickle'):
        with pytest.raises(TypeError):
            (copy.copy if fault == 'copy' else pickle.dumps)(s.receipt)
        return
    if fault == 'detached': s.receipt = BASE['verify'](s, s.receipt)
    elif fault == 'new_handle': s.receipt = SuccessfulB3Receipt()
    elif fault == 'execution_cap': s.receipt = s.cap
    elif fault == 'result': s.receipt = s.returned[0].result
    else: s.receipt = make().receipt
    with pytest.raises(ForecastWriteIntentError): issue(s)
    assert not s.write._grants


def test_constructor_and_per_call_host_boundaries(make):
    s = make(); h = issue(s)
    for p in (copy.copy(s.producer), SimpleNamespace(_self=s.producer), object()):
        with pytest.raises(ForecastWriteIntentError): ForecastWriteIntentAuthority(p, operator=object())
    for operator in ('host', object(), s.operator, s.execution_operator):
        with pytest.raises(ForecastWriteIntentError): s.write.revoke(operator, h)
    with pytest.raises(ForecastWriteIntentError):
        copy.copy(s.write).verify(h, deadline=150.)
    with pytest.raises(TypeError): s.write.issue(s.write_operator, producer=s.producer, result=s.returned[0])
    with pytest.raises(TypeError): s.write.verify(h, producer=make().producer, deadline=150.)
    # A hostile instance alias is ignored: the ORIGINAL class verifier is used.
    s.producer.verify = lambda *a, **kw: BASE['verify'](make(), s.receipt)
    assert verify(s, h).state == 'ISSUED'


@pytest.mark.parametrize('mode', ['DENIED', 'UNAVAILABLE', 'exception'])
def test_failed_consumed_b3_never_authorizes_write(make, mode):
    s = make(produce=False); s.control['mode'] = mode
    with pytest.raises(ProducerEvidenceError): BASE['issue'](s)
    assert s.authority._grants[id(s.cap)].state == 'CONSUMED' and not s.producer._issued
    s.receipt = s.cap
    with pytest.raises(ForecastWriteIntentError): issue(s)
    assert not s.write._grants


@pytest.mark.parametrize('component', ['reference', 'native', 'inputs'])
def test_original_owner_and_envelope_identity_required(make, component):
    s = make(); setattr(s, component, object())
    with pytest.raises(ForecastWriteIntentError): issue(s)
    assert not s.write._grants


@pytest.mark.parametrize('field', ['candidate', 'family', 'snapshot', 'tenant', 'series', 'publication', 'origin', 'cutoff', 'frozen'])
def test_scope_substitution_cannot_issue(make, field):
    s = make(); r = s.request; b = r.inputs; request = b.request; sr = request.snapshot_request
    if field in ('candidate', 'family'):
        r = r.model_copy(update={('candidate_id' if field == 'candidate' else 'family'): ('f'*64 if field == 'candidate' else 'gru')})
    elif field in ('snapshot', 'frozen'):
        b = b.model_copy(update={'request': request.model_copy(update={('snapshot_id' if field == 'snapshot' else 'frozen_state_id'): 'f'*64})})
    else:
        if field == 'tenant': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'reference': replace(sr.dataset.reference, organization_id=UUID(int=999))})})
        elif field == 'series': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'series_ids': sr.dataset.series_ids[:1]})})
        elif field == 'publication': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'admission_digest': 'f'*64})})
        else: sr = sr.model_copy(update={('forecast_origin' if field == 'origin' else 'capture_cutoff'): sr.forecast_origin+timedelta(days=1)})
        b = b.model_copy(update={'request': request.model_copy(update={'snapshot_request': sr})})
    if field not in ('candidate', 'family'): r = r.model_copy(update={'inputs': b})
    s.request = r
    with pytest.raises(ForecastWriteIntentError): issue(s)
    assert not s.write._grants


@pytest.mark.parametrize('fault', ['new_handle', 'object', 'execution', 'receipt', 'view', 'foreign', 'copy', 'pickle'])
def test_write_capability_original_issuance_only(make, fault):
    s = make(); h = issue(s)
    if fault in ('copy', 'pickle'):
        with pytest.raises(TypeError): (copy.copy if fault == 'copy' else pickle.dumps)(h)
        return
    other = make()
    value = {'new_handle': ForecastWriteCapability(), 'object': object(), 'execution': s.cap,
        'receipt': s.receipt, 'view': verify(s, h), 'foreign': issue(other)}[fault]
    with pytest.raises(ForecastWriteIntentError): consume(s, value)
    assert s.write._grants[id(h)].state == 'ISSUED'


@pytest.mark.parametrize('field', ['rows', 'bits', 'digest', 'shape', 'dtype', 'key', 'duplicate', 'origin', 'target', 'tenant', 'correction'])
def test_record_schema_order_keys_bits_and_geometry_reject(make, field):
    s = make(); r = verify(s, issue(s)).records; data = json.loads(r.canonical_json)
    if field == 'rows': data['rows'].reverse()
    elif field == 'bits': data['rows'][0]['float32_le'] = '00000000'
    elif field == 'digest': data['row_set_sha256'] = 'f'*64
    elif field == 'shape': data['shape'] = [3]
    elif field == 'dtype': data['physical_dtype'] = 'float64'
    elif field == 'key': data['rows'][0]['logical_key_id'] = 'f'*64
    elif field == 'duplicate': data['rows'][1] = data['rows'][0]
    elif field == 'correction': data['correction_policy'] = 'SUPERSEDE'
    elif field == 'tenant': data['rows'][0]['scope']['organization_id'] = str(UUID(int=999))
    else: data['rows'][0]['scope'][('forecast_origin' if field == 'origin' else 'target_period_end')] = '2030-01-01T00:00:00Z'
    with pytest.raises(ValueError): B3ForecastRecordSet.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('bits', ['0000807f', '000080ff', '0000c07f', 'zzzzzzzz', '0000', '0000000000'])
def test_invalid_or_nonfinite_physical_float32_reject(make, bits):
    s = make(); row = verify(s, issue(s)).records.rows[0]
    with pytest.raises(ValueError): B3ForecastRow(scope=row.scope, logical_key_id=row.logical_key_id, float32_le=bits)


def test_detached_mutation_cannot_change_retained_records(make):
    s = make(); h = issue(s); first = verify(s, h); encoded = first.canonical_json
    object.__setattr__(first.records.rows[0], 'float32_le', '00000000')
    object.__setattr__(first.records, 'row_set_sha256', 'f'*64)
    assert verify(s, h).canonical_json == encoded
    g = s.write._grants[id(h)]; data = json.loads(g.records_json)
    data['rows'][0]['float32_le'] = '00000000'
    object.__setattr__(g, 'records_json', json.dumps(data))
    with pytest.raises(ForecastWriteIntentError): verify(s, h)


def test_idempotency_reuse_same_or_conflicting_content_rejects_without_reissue(make):
    s = make(); h = issue(s); first = verify(s, h)
    with pytest.raises(ForecastWriteIntentError): issue(s)
    s.cap = s.authority.issue(s.execution_operator, s.reference, s.native, s.inputs, s.request)
    s.receipt = BASE['issue'](s, lifetime_seconds=60)
    assert BASE['verify'](s, s.receipt).receipt_id != first.records.producer.receipt_id
    with pytest.raises(ForecastWriteIntentError): issue(s)
    assert len(s.write._grants) == 1 and len(s.write._idempotency) == 1
    assert consume(s, h).records.record_set_id == first.records.record_set_id
    with pytest.raises(ForecastWriteIntentError): issue(s)


@pytest.mark.parametrize('fault', ['grant', 'producer_receipt', 'producer', 'owner', 'execution_grant', 'expiry', 'deadline', 'issuer'])
def test_live_revocation_expiry_and_deadline(make, fault):
    s = make(); h = issue(s, lifetime_seconds=20)
    if fault == 'grant': assert s.write.revoke(s.write_operator, h)
    elif fault == 'producer_receipt': s.producer.revoke(s.operator, s.receipt)
    elif fault == 'producer': s.producer.invalidate(s.operator)
    elif fault == 'owner': s.control['owner'] = False
    elif fault == 'execution_grant': s.authority.revoke(s.execution_operator, s.cap)
    elif fault == 'issuer': s.write.invalidate(s.write_operator)
    else: s.control['wall'] = 120.
    with pytest.raises(ForecastWriteIntentError): consume(s, h, deadline=120. if fault == 'deadline' else 200.)


def test_pre_reservation_rejection_and_permanent_post_reservation_failure(make):
    s = make(); h = issue(s)
    with pytest.raises(ForecastWriteIntentError): consume(s, h, cancelled=lambda: True)
    assert s.write._grants[id(h)].state == 'ISSUED' and verify(s, h)
    saw_reservation = []
    def interrupted():
        if s.write._grants[id(h)].state == 'CONSUMED':
            saw_reservation.append(True)
            return True
        return False
    with pytest.raises(ForecastWriteIntentError): consume(s, h, cancelled=interrupted)
    assert saw_reservation and s.write._grants[id(h)].reservation is not None
    assert s.write._grants[id(h)].state == 'CONSUMED'
    with pytest.raises(ForecastWriteIntentError): consume(s, h)


@pytest.mark.parametrize('operation', ['issue', 'verify', 'consume'])
@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed', 'producer_wall', 'producer_elapsed'])
def test_dynamic_final_callback_revocation(make, operation, edge):
    def run(s, target=None):
        h = issue(s) if operation != 'issue' else None
        counts = dict.fromkeys(('cancel', 'wall', 'elapsed', 'producer_wall', 'producer_elapsed'), 0)
        wall = s.write._wall; elapsed = s.write._elapsed
        pw = s.producer._wall; pe = s.producer._elapsed
        def poll(key, value):
            counts[key] += 1
            if target is not None and key == edge and counts[key] == target:
                if h: assert s.write.revoke(s.write_operator, h)
                else: s.write.invalidate(s.write_operator)
            return value
        s.write._wall = lambda: poll('wall', wall())
        s.write._elapsed = lambda: poll('elapsed', elapsed())
        s.producer._wall = lambda: poll('producer_wall', pw())
        s.producer._elapsed = lambda: poll('producer_elapsed', pe())
        try:
            value = (issue(s, cancelled=lambda: poll('cancel', False)) if h is None else
                     (verify if operation == 'verify' else consume)(s, h, cancelled=lambda: poll('cancel', False)))
            return value, counts
        finally:
            s.observed = counts
    _, counts = run(make()); target = counts[edge]; assert target >= 2
    s = make()
    with pytest.raises(ForecastWriteIntentError): run(s, target)
    assert s.observed[edge] == target
    if operation == 'issue': assert not s.write._grants


@pytest.mark.parametrize('operation', ['issue', 'verify', 'consume'])
@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed'])
@pytest.mark.parametrize('action', ['receipt', 'owner'])
def test_final_callback_revokes_producer_or_owner(make, operation, edge, action):
    def run(s, target=None):
        h = issue(s) if operation != 'issue' else None
        counts = dict(cancel=0, wall=0, elapsed=0)
        def poll(key, value):
            counts[key] += 1
            if key == edge and counts[key] == target:
                if action == 'receipt': s.producer.revoke(s.operator, s.receipt)
                else: s.control['owner'] = False
            return value
        s.write._wall = lambda: poll('wall', s.control['wall'])
        s.write._elapsed = lambda: poll('elapsed', s.control['elapsed'])
        try:
            if h is None: issue(s, cancelled=lambda: poll('cancel', False))
            else: (verify if operation == 'verify' else consume)(s, h, cancelled=lambda: poll('cancel', False))
            return counts
        finally:
            s.observed = counts
    target = run(make())[edge]; assert target >= 2
    s = make()
    with pytest.raises(ForecastWriteIntentError): run(s, target)
    assert s.observed[edge] == target


def test_replacement_of_original_issuance_during_final_producer_clock_rejects(make):
    probe = make(); ph = issue(probe); samples = []
    probe.producer._wall = lambda: (samples.append(1), probe.control['wall'])[1]
    verify(probe, ph)
    target = len(samples); assert target >= 2
    s = make(); h = issue(s); original = s.producer._issued[id(s.receipt)]; calls = []
    def clock():
        calls.append(1)
        if len(calls) == target:
            s.producer._issued[id(s.receipt)] = replace(original)  # equal value, different retained record
        return s.control['wall']
    s.producer._wall = clock
    with pytest.raises(ForecastWriteIntentError): consume(s, h)
    assert len(calls) == target and s.write._grants[id(h)].state == 'CONSUMED'


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), 'PRIVATE_CLOCK'])
def test_invalid_clock_is_sanitized_and_no_authority_issued(make, value):
    s = make(); s.write._wall = lambda: value
    with pytest.raises(ForecastWriteIntentError) as e: issue(s)
    assert 'PRIVATE' not in str(e.value) and e.value.__context__ is None
    assert not s.write._grants


@pytest.mark.parametrize('fault', ['cancel', 'deadline', 'elapsed', 'rollback'])
def test_cancellation_deadline_elapsed_and_rollback_fail_closed(make, fault):
    s = make(); h = issue(s)
    if fault == 'cancel': args = dict(cancelled=lambda: True)
    elif fault == 'deadline': args = dict(deadline=100.)
    elif fault == 'rollback': s.control['wall'] = 99.; args = {}
    else:
        ticks = iter([0., 30.]); s.write._elapsed = lambda: next(ticks); args = {}
    with pytest.raises(ForecastWriteIntentError): consume(s, h, **args)
    assert s.write._grants[id(h)].state == 'ISSUED'


@pytest.mark.parametrize('kwargs', [{'lifetime_seconds': 0}, {'lifetime_seconds': 61}, {'lifetime_seconds': True},
                                  {'idempotency_id': ''}, {'idempotency_id': object()}, {'deadline': None}])
def test_invalid_issuance_bounds(make, kwargs):
    s = make()
    with pytest.raises(ForecastWriteIntentError): issue(s, **kwargs)
    assert not s.write._grants


def test_registry_and_encoding_bounds_no_eviction(make):
    s = make(limits=ForecastWriteIntentLimits(max_grants=1)); h = issue(s)
    with pytest.raises(ForecastWriteIntentError): issue(s, idempotency_id='d'*64)
    assert verify(s, h)
    small = make(limits=ForecastWriteIntentLimits(max_record_bytes=1))
    with pytest.raises(ForecastWriteIntentError): issue(small)
    assert not small.write._grants


def test_no_storage_native_execution_or_legacy_fallback(make, monkeypatch):
    s = make(); calls = s.calls
    def no(*args, **kwargs): raise AssertionError('forbidden operation')
    import src.services.successful_b3_producer_service as producer
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    monkeypatch.setattr(sqlite3, 'connect', no)
    monkeypatch.setattr(producer, '_EXECUTE', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'load', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'predict', no)
    h = issue(s); out = consume(s, h)
    assert s.calls == calls and not out.writes_performed and out.durable_receipt is None
    assert not hasattr(s.write, 'persist') and not hasattr(s.write, 'execute')


def test_reentrant_callbacks_and_untrusted_operator_do_not_deadlock_or_coerce(make):
    s = make(); observed = []
    class Hostile:
        def __str__(self): raise AssertionError('coercion')
        def __repr__(self): raise AssertionError('repr')
    with pytest.raises(ForecastWriteIntentError):
        s.write.issue(Hostile(), s.receipt, s.reference, s.native, s.inputs, s.request,
                      idempotency_id='c'*64, deadline=150.)
    def callback():
        with pytest.raises(ForecastWriteIntentError): issue(s)
        observed.append(True)
        return False
    h = issue(s, cancelled=callback)
    assert observed and verify(s, h)


@pytest.mark.parametrize('field', ['writes_performed', 'persistence_completed', 'durable_receipt', 'production_permission'])
def test_public_view_cannot_claim_commit_or_production_authority(make, field):
    s = make(); v = verify(s, issue(s)); data = json.loads(v.canonical_json)
    data[field] = 'forged' if field == 'durable_receipt' else True
    with pytest.raises(ValueError): ValidatedForecastWriteIntent.model_validate_json(json.dumps(data))


def test_duplicate_json_fields_and_unsupported_correction_reject(make):
    s = make(); v = verify(s, issue(s))
    with pytest.raises(ValueError): ValidatedForecastWriteIntent.model_validate_json(v.canonical_json.replace('"version":"1.0"', '"version":"1.0","version":"1.0"', 1))
    data = json.loads(v.grant.canonical_json); data['correction_policy'] = 'OVERWRITE'
    with pytest.raises(ValueError): ForecastWriteGrantDeclaration.model_validate_json(json.dumps(data))


def test_coherently_recomputed_public_records_do_not_establish_trusted_origin(make):
    s = make(); h = issue(s); v = verify(s, h); p = v.records.producer
    predictions = tuple(reversed(p.execution.predictions))
    execution = p.execution.model_copy(update={'predictions': predictions,
        'prediction_sha256': hashlib.sha256(struct.pack('<4f', *predictions)).hexdigest()})
    # All public content can be rewritten consistently. It is still not the
    # ORIGINAL retained producer result and cannot establish authority.
    prows = tuple(ProducerRow(scope=r.scope, float32_le=struct.pack('<f', value).hex())
                  for r, value in zip(p.rows, predictions))
    changed = p.model_copy(update={'execution': execution, 'rows': prows,
                                  'result_set_sha256': result_set_identity(execution, prows)})
    changed = type(p).model_validate_json(changed.canonical_json)
    rows = tuple(B3ForecastRow(scope=r.scope, float32_le=r.float32_le,
                logical_key_id=forecast_logical_key(changed.execution, r.scope)) for r in changed.rows)
    records = B3ForecastRecordSet(producer=changed, idempotency_id=v.records.idempotency_id,
        rows=rows, shape=changed.shape, row_set_sha256=forecast_row_set_identity(changed, rows))
    assert records.record_set_id != v.records.record_set_id
    with pytest.raises(ForecastWriteIntentError): consume(s, records)
    s.receipt = changed
    with pytest.raises(ForecastWriteIntentError): issue(s, idempotency_id='d'*64)
    object.__setattr__(s.write._grants[id(h)], 'records_json', records.canonical_json)
    with pytest.raises(ForecastWriteIntentError): consume(s, h)


@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed'])
def test_actual_final_interruption_consumes_reserved_attempt(make, edge):
    probe = make(); ph = issue(probe); counts = dict(cancel=0, wall=0, elapsed=0)
    def count(k, v): counts[k] += 1; return v
    probe.write._wall = lambda: count('wall', probe.control['wall'])
    probe.write._elapsed = lambda: count('elapsed', probe.control['elapsed'])
    verify(probe, ph, cancelled=lambda: count('cancel', False))
    target = counts[edge]
    s = make(); h = issue(s); observed = 0
    def interrupt(value):
        nonlocal observed
        observed += 1
        if observed == target: return {'cancel': True, 'wall': 130., 'elapsed': 30.}[edge]
        return value
    if edge == 'wall': s.write._wall = lambda: interrupt(s.control['wall'])
    if edge == 'elapsed': s.write._elapsed = lambda: interrupt(s.control['elapsed'])
    with pytest.raises(ForecastWriteIntentError):
        consume(s, h, cancelled=(lambda: interrupt(False)) if edge == 'cancel' else lambda: False)
    assert observed == target and s.write._grants[id(h)].state == 'CONSUMED'
    with pytest.raises(ForecastWriteIntentError): consume(s, h)


def test_successful_fact_outlives_execution_window_but_not_producer_expiry(make):
    s = make(); s.control['wall'] = 140.
    h = issue(s, deadline=180., lifetime_seconds=60)
    v = verify(s, h)
    assert v.grant.expires_at == 160. and v.grant.write_deadline == 160.
    assert s.authority._grants[id(s.cap)].declaration.execution_deadline == 130.
    s.control['wall'] = 160.
    with pytest.raises(ForecastWriteIntentError): consume(s, h)
    assert s.write._grants[id(h)].state == 'ISSUED'


def test_original_host_cancellation_source_remains_bound_to_the_grant(make):
    s = make(); state = dict(cancel=False)
    h = issue(s, cancelled=lambda: state['cancel'])
    assert verify(s, h).grant.cancellation_policy == 'host_bound_and_operation_cooperative_v1'
    state['cancel'] = True
    with pytest.raises(ForecastWriteIntentError): verify(s, h)
    with pytest.raises(ForecastWriteIntentError): consume(s, h, cancelled=lambda: False)
    assert s.write._grants[id(h)].state == 'ISSUED'
    state['cancel'] = False
    assert consume(s, h).state == 'CONSUMED_NO_WRITE'
