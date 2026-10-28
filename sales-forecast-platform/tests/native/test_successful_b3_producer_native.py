"""Genuine successful producer origin, only in the approved disposable P0 worker."""
import hashlib
import copy
import json
from pathlib import Path
import runpy
import struct
import sys

import pytest
from src.services.controlled_execution_authority import ControlledExecutionAuthority
from src.services.controlled_h1_execution_service import ControlledH1ExecutionService
from src.services.successful_b3_producer_service import ProducerEvidenceError, SuccessfulB3ProducerService

BASE = runpy.run_path('tests/runtime/test_controlled_h1_execution.py')


@pytest.fixture(scope='module', autouse=True)
def isolated_only():
    generator = BASE['isolated_only'].__wrapped__()
    next(generator)
    try:
        yield
    finally:
        with pytest.raises(StopIteration): next(generator)


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    generator = BASE['genuine'].__wrapped__(tmp_path_factory)
    result = next(generator)
    try:
        yield result
    finally:
        with pytest.raises(StopIteration): next(generator)


@pytest.fixture
def context(genuine):
    s = dict(genuine)
    s['trainer']._candidates[id(s['ref'])] = s['record']
    control = dict(wall=100., elapsed=0.)
    operator = object()
    authority = ControlledExecutionAuthority(s['provider'], operator=operator, wall_clock=lambda: control['wall'])
    service = ControlledH1ExecutionService(authority, elapsed_clock=lambda: control['elapsed'])
    producer_operator = object()
    producer = SuccessfulB3ProducerService(service, operator=producer_operator,
        wall_clock=lambda: control['wall'], elapsed_clock=lambda: control['elapsed'])
    cap = authority.issue(operator, s['ref'], s['native'], s['inputs'], s['request'])
    s.update(control=control, operator=operator, authority=authority, service=service, cap=cap,
             producer_operator=producer_operator, producer=producer)
    try:
        yield s
    finally:
        s['trainer']._candidates[id(s['ref'])] = s['record']


def produce(s, **kw):
    return s['producer'].produce(s['producer_operator'], s['ref'], s['native'], s['inputs'], s['request'],
                                 capability=s['cap'], deadline=130., **kw)


def verify(s, h, **kw):
    return s['producer'].verify(h, s['ref'], s['native'], s['inputs'], s['request'], deadline=130., **kw)


def profile_returns(operation):
    returns = []
    code = ControlledH1ExecutionService.execute.__code__
    previous = sys.getprofile()
    def observed(frame, event, value):
        if event == 'return' and frame.f_code is code:
            returns.append(value)
    sys.setprofile(observed)
    try:
        result = operation(returns)
    finally:
        sys.setprofile(previous)
    return result, returns


def test_genuine_original_return_retained_and_receipt_verified(context, monkeypatch):
    s = context
    root = s['b']['store'].root
    inventory = lambda: {str(p):(p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                         for p in root.rglob('*') if p.is_file()}
    before = inventory()
    def no(*a, **kw): raise AssertionError('no refit or input preparation at producer execution')
    from src.training.canonical_adapters import NativeBackend
    from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor
    monkeypatch.setattr(NativeBackend, 'fit', no)
    monkeypatch.setattr(FrozenTabularPreprocessor, 'transform', no)
    h, returns = profile_returns(lambda _: produce(s))
    assert len(returns) == 1 and returns[0].status == 'EXECUTED'
    record = s['producer']._issued[id(h)]
    assert record.outcome is returns[0] and record.result is returns[0].result
    assert record.reference is s['ref'] and record.native is s['native'] and record.inputs is s['inputs']
    assert record.owner_record is s['record']
    assert s['native_owner']._issued[id(s['native'])][1] is s['record']
    assert s['prep']._issued[id(s['inputs'])][1] is s['ref']
    assert s['prep']._issued[id(s['inputs'])][2] is s['record']
    assert record.grant_record is s['authority']._grants[id(s['cap'])]
    assert record.grant_record.state == 'CONSUMED'
    d = verify(s, h)
    assert d.execution.canonical_json == returns[0].result.canonical_json
    assert verify(s, h).canonical_bytes == d.canonical_bytes
    assert [r.scope for r in d.rows] == [g[0] for g in s['inputs'].output.representation.samples]
    assert b''.join(bytes.fromhex(r.float32_le) for r in d.rows) == b''.join(struct.pack('<f', v) for v in record.result.predictions)
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    monkeypatch.setattr(XGBoostH1Adapter, 'load', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'predict', no)
    assert verify(s, h)
    # Neither a consumed execution grant nor a successful producer receipt can
    # authorize another execution. Both fail before a parser/predictor call.
    for cap in (s['cap'], h, d):
        out = s['service'].execute(s['ref'], s['native'], s['inputs'], s['request'], capability=cap)
        assert out.status == 'DENIED' and out.result is None
    assert inventory() == before and not d.write_permission and not d.persistable
    print('SUCCESSFUL_B3_PRODUCER_NATIVE', json.dumps({'candidate_id': d.execution.request.candidate_id,
        'artifact_id': s['native'].manifest.artifact_id, 'payload_sha256': s['native'].manifest.payload_sha256,
        'snapshot_id': d.execution.request.inputs.request.snapshot_id, 'input_binding_id': s['inputs'].binding.binding_id,
        'result_id': d.execution.result_id, 'receipt_id': d.receipt_id, 'result_set_sha256': d.result_set_sha256,
        'predictions': d.execution.predictions, 'float32_le': [r.float32_le for r in d.rows],
        'shape': d.shape, 'exact_original_return_retained': True, 'execution_grant_replay_denied': True,
        'receipt_is_not_execution_or_write_authority': True}, sort_keys=True))
    assert s['authority'].revoke(s['operator'], s['cap'])
    with pytest.raises(ProducerEvidenceError): verify(s, h)


@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed'])
@pytest.mark.parametrize('action', ['grant', 'owner'])
def test_genuine_post_b3_terminal_grant_revocation_no_receipt(context, edge, action):
    s = context
    returns = []
    original_wall = s['producer']._wall
    original_elapsed = s['producer']._elapsed
    revoked = []
    def callback(value):
        if returns and not revoked:
            assert returns[-1].status == 'EXECUTED'
            if action == 'grant': assert s['authority'].revoke(s['operator'], s['cap'])
            else: assert s['trainer'].discard(s['ref'])
            revoked.append(True)
        return value
    if edge == 'wall': s['producer']._wall = lambda: callback(original_wall())
    if edge == 'elapsed': s['producer']._elapsed = lambda: callback(original_elapsed())
    previous = sys.getprofile()
    def observed(frame, event, value):
        if event == 'return' and frame.f_code is ControlledH1ExecutionService.execute.__code__: returns.append(value)
    sys.setprofile(observed)
    try:
        with pytest.raises(ProducerEvidenceError):
            produce(s, cancelled=(lambda: callback(False)) if edge == 'cancel' else lambda: False)
    finally:
        sys.setprofile(previous)
    assert revoked and returns[-1].status == 'EXECUTED' and not s['producer']._issued


@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed'])
def test_genuine_receipt_terminal_revocation_no_evidence(context, edge):
    s = context; h = produce(s); counts = dict(cancel=0, wall=0, elapsed=0)
    wall = s['producer']._wall; elapsed = s['producer']._elapsed
    def count(key, value): counts[key] += 1; return value
    s['producer']._wall = lambda: count('wall', wall())
    s['producer']._elapsed = lambda: count('elapsed', elapsed())
    verify(s, h, cancelled=lambda: count('cancel', False))
    total = counts[edge]; assert total >= 2
    observed = 0
    def revoke(value):
        nonlocal observed
        observed += 1
        if observed == total: assert s['producer'].revoke(s['producer_operator'], h)
        return value
    s['producer']._wall = (lambda: revoke(wall())) if edge == 'wall' else wall
    s['producer']._elapsed = (lambda: revoke(elapsed())) if edge == 'elapsed' else elapsed
    with pytest.raises(ProducerEvidenceError):
        verify(s, h, cancelled=(lambda: revoke(False)) if edge == 'cancel' else lambda: False)
    assert observed == total


def test_genuine_failed_but_consumed_missing_dependency_cannot_issue(context, monkeypatch):
    s = context
    captured = []
    def operation(returns):
        try: return produce(s)
        finally: captured.extend(returns)
    def missing(*a, **kw): raise ModuleNotFoundError('PRIVATE_NATIVE_PATH')
    monkeypatch.setattr('src.execution.controlled_h1_adapters.importlib.import_module', missing)
    with pytest.raises(ProducerEvidenceError):
        profile_returns(operation)
    assert len(captured) == 1 and captured[0].status == 'UNAVAILABLE' and captured[0].result is None
    assert not s['producer']._issued and s['authority']._grants[id(s['cap'])].state == 'CONSUMED'
    monkeypatch.undo()
    with pytest.raises(ProducerEvidenceError): produce(s)
    assert not s['producer']._issued


@pytest.mark.parametrize('component', ['owner', 'native', 'inputs'])
def test_genuine_copied_original_ownership_rejected(context, component):
    from src.data.contracts.executable_inputs import OwnedInputEnvelope
    from src.data.contracts.native_xgboost_roundtrip import NativeArtifactEnvelope
    s = context; reference = s['ref']; native = s['native']; inputs = s['inputs']
    if component == 'owner': reference = copy.copy(reference)
    elif component == 'native': native = NativeArtifactEnvelope(native.manifest, native.payload)
    else: inputs = OwnedInputEnvelope(inputs.binding, inputs.output)
    with pytest.raises(ProducerEvidenceError):
        s['producer'].produce(s['producer_operator'], reference, native, inputs, s['request'],
                              capability=s['cap'], deadline=130.)
    assert not s['producer']._issued
