"""Genuine B3 producer -> independent write intent; STOP before any database.

Only the accepted P0 recovered-image Docker launcher may run this module.
Synthetic catalog creation belongs to the unchanged accepted fixture in worker
tmpfs. The new write-intent authority opens no catalog or model.
"""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import struct

import numpy as np
import pytest
from src.services.forecast_write_intent_authority import ForecastWriteIntentAuthority, ForecastWriteIntentError
from src.services.successful_b3_producer_service import ProducerEvidenceError

BASE = runpy.run_path('tests/native/test_successful_b3_producer_native.py')


@pytest.fixture(scope='module', autouse=True)
def isolated_only():
    generator = BASE['isolated_only'].__wrapped__(); next(generator)
    try:
        yield
    finally:
        with pytest.raises(StopIteration): next(generator)


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    generator = BASE['genuine'].__wrapped__(tmp_path_factory); result = next(generator)
    try:
        yield result
    finally:
        with pytest.raises(StopIteration): next(generator)


@pytest.fixture
def context(genuine):
    generator = BASE['context'].__wrapped__(genuine); s = next(generator)
    operator = object()
    s['write_operator'] = operator
    s['write'] = ForecastWriteIntentAuthority(s['producer'], operator=operator,
        wall_clock=lambda: s['control']['wall'], elapsed_clock=lambda: s['control']['elapsed'])
    try:
        yield s
    finally:
        with pytest.raises(StopIteration): next(generator)


def issue(s, receipt, **kwargs):
    args = dict(idempotency_id='c'*64, deadline=150.); args.update(kwargs)
    return s['write'].issue(s['write_operator'], receipt, s['ref'], s['native'], s['inputs'], s['request'], **args)


def test_genuine_original_success_independent_write_intent_exact_bits_no_write(context, monkeypatch):
    s = context
    receipt, outcomes = BASE['profile_returns'](lambda _: BASE['produce'](s, lifetime_seconds=60))
    assert len(outcomes) == 1 and outcomes[0].status == 'EXECUTED'
    producer_record = s['producer']._issued[id(receipt)]
    assert producer_record.outcome is outcomes[0] and producer_record.result is outcomes[0].result
    assert producer_record.owner_record is s['record']
    assert producer_record.reference is s['ref'] and producer_record.native is s['native'] and producer_record.inputs is s['inputs']
    assert producer_record.grant_record is s['authority']._grants[id(s['cap'])]
    assert producer_record.grant_record.state == 'CONSUMED'
    with pytest.raises(ForecastWriteIntentError): s['write'].verify(receipt, deadline=150.)
    with pytest.raises(ForecastWriteIntentError): s['write'].verify(s['cap'], deadline=150.)

    # Independent native references are confined to this authorized synthetic worker.
    import xgboost as xgb
    from src.data.contracts.model_candidate import strict_float32
    assert xgb.__version__ == '3.2.0'
    output = s['prep'].require(s['inputs']); matrix = strict_float32(output.values)
    original = s['record'][3].predict(matrix)
    reloaded = xgb.Booster(params={'nthread': 1}); reloaded.load_model(bytearray(s['native'].payload))
    reference = reloaded.predict(xgb.DMatrix(matrix, feature_names=list(output.matrix.columns), nthread=1), validate_features=True)
    root = s['b']['store'].root
    inventory = lambda: {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                         for p in root.rglob('*') if p.is_file()}
    before = inventory()
    def no(*args, **kwargs): raise AssertionError('no database, load, prediction, fitting or preparation at write boundary')
    from src.training.canonical_adapters import NativeBackend
    from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    monkeypatch.setattr(sqlite3, 'connect', no)
    monkeypatch.setattr(NativeBackend, 'fit', no)
    monkeypatch.setattr(FrozenTabularPreprocessor, 'transform', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'load', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'predict', no)
    handle = issue(s, receipt)
    g = s['write']._grants[id(handle)]
    assert g.producer_record is producer_record and g.receipt is receipt
    first = s['write'].verify(handle, deadline=150.)
    assert s['write'].verify(handle, deadline=150.).canonical_bytes == first.canonical_bytes
    result = s['write'].simulate_consume(handle, deadline=150.)
    assert result.state == 'CONSUMED_NO_WRITE' and not result.writes_performed
    assert not result.persistence_completed and result.durable_receipt is None
    assert result.records.record_set_id == first.records.record_set_id
    raw = b''.join(bytes.fromhex(r.float32_le) for r in result.records.rows)
    actual = np.frombuffer(raw, dtype='<f4')
    assert actual.shape == (4,) and actual.dtype == np.dtype('float32') and np.isfinite(actual).all()
    assert original.astype('<f4').tobytes() == reference.astype('<f4').tobytes() == raw
    assert raw == b''.join(struct.pack('<f', v) for v in outcomes[0].result.predictions)
    assert struct.pack('<4f', *(r.prediction_value for r in result.records.rows)) == raw
    assert [r.scope for r in result.records.rows] == [grid[0] for grid in output.representation.samples]
    assert len({r.logical_key_id for r in result.records.rows}) == 4 and inventory() == before
    for cap in (handle, result, receipt, s['cap']):
        with pytest.raises(ForecastWriteIntentError): s['write'].simulate_consume(cap, deadline=150.)
    print('B3_PRODUCER_WRITE_INTENT_NATIVE', json.dumps({
        'framework': xgb.__version__, 'payload_bytes': len(s['native'].payload),
        'payload_sha256': hashlib.sha256(s['native'].payload).hexdigest(),
        'candidate_id': s['request'].candidate_id, 'snapshot_id': s['request'].inputs.request.snapshot_id,
        'receipt_id': result.records.producer.receipt_id, 'record_set_id': result.records.record_set_id,
        'row_set_sha256': result.records.row_set_sha256, 'logical_keys': [r.logical_key_id for r in result.records.rows],
        'predictions': actual.tolist(), 'float32_le': [r.float32_le for r in result.records.rows],
        'shape': list(actual.shape), 'dtype': str(actual.dtype),
        'max_absolute_difference': float(np.max(np.abs(actual-reference))),
        'max_relative_difference': float(np.max(np.abs((actual-reference)/reference))),
        'same_original_owner_and_result': True, 'original_handle_verified': True,
        'independent_write_grant_consumed_once': True, 'database_operations_at_write_boundary': 0,
        'writes_performed': result.writes_performed}, sort_keys=True))


@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed', 'producer_wall', 'producer_elapsed'])
def test_genuine_final_callback_revokes_write_intent_no_success(context, edge):
    s = context; receipt = BASE['produce'](s, lifetime_seconds=60)
    handle = issue(s, receipt)
    counts = dict.fromkeys(('cancel', 'wall', 'elapsed', 'producer_wall', 'producer_elapsed'), 0)
    w, e, pw, pe = s['write']._wall, s['write']._elapsed, s['producer']._wall, s['producer']._elapsed
    def poll(key, value): counts[key] += 1; return value
    s['write']._wall = lambda: poll('wall', w())
    s['write']._elapsed = lambda: poll('elapsed', e())
    s['producer']._wall = lambda: poll('producer_wall', pw())
    s['producer']._elapsed = lambda: poll('producer_elapsed', pe())
    s['write'].verify(handle, deadline=150., cancelled=lambda: poll('cancel', False))
    target = counts[edge]; assert target >= 2
    observed = 0
    def revoke(value):
        nonlocal observed
        observed += 1
        if observed == target: assert s['write'].revoke(s['write_operator'], handle)
        return value
    s['write']._wall = (lambda: revoke(w())) if edge == 'wall' else w
    s['write']._elapsed = (lambda: revoke(e())) if edge == 'elapsed' else e
    s['producer']._wall = (lambda: revoke(pw())) if edge == 'producer_wall' else pw
    s['producer']._elapsed = (lambda: revoke(pe())) if edge == 'producer_elapsed' else pe
    with pytest.raises(ForecastWriteIntentError):
        s['write'].simulate_consume(handle, deadline=150.,
            cancelled=(lambda: revoke(False)) if edge == 'cancel' else lambda: False)
    assert observed == target and s['write']._grants[id(handle)].state == 'REVOKED'
    with pytest.raises(ForecastWriteIntentError): s['write'].simulate_consume(handle, deadline=150.)


@pytest.mark.parametrize('component', ['owner', 'native', 'inputs', 'receipt', 'consumed_execution'])
def test_genuine_original_identity_substitution_denied(context, component):
    from src.data.contracts.executable_inputs import OwnedInputEnvelope
    from src.data.contracts.native_xgboost_roundtrip import NativeArtifactEnvelope
    from src.data.contracts.successful_b3_producer import SuccessfulB3Receipt
    s = context; receipt = BASE['produce'](s, lifetime_seconds=60)
    reference, native, inputs = s['ref'], s['native'], s['inputs']
    if component == 'owner': reference = copy.copy(reference)
    elif component == 'native': native = NativeArtifactEnvelope(native.manifest, native.payload)
    elif component == 'inputs': inputs = OwnedInputEnvelope(inputs.binding, inputs.output)
    elif component == 'receipt': receipt = SuccessfulB3Receipt()
    else: receipt = s['cap']
    with pytest.raises(ForecastWriteIntentError):
        s['write'].issue(s['write_operator'], receipt, reference, native, inputs, s['request'],
                          idempotency_id='c'*64, deadline=150.)
    assert not s['write']._grants


def test_genuine_failed_consumed_execution_has_no_write_authority(context, monkeypatch):
    s = context
    def missing(*args, **kwargs): raise ModuleNotFoundError('private native failure')
    monkeypatch.setattr('src.execution.controlled_h1_adapters.importlib.import_module', missing)
    with pytest.raises(ProducerEvidenceError): BASE['produce'](s)
    assert not s['producer']._issued and s['authority']._grants[id(s['cap'])].state == 'CONSUMED'
    with pytest.raises(ForecastWriteIntentError): issue(s, s['cap'])
    assert not s['write']._grants
