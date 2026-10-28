"""Producer contract tests with an explicitly mocked B3 execution function.

The M3 input fixture is real, but native ownership/execution here is NOT genuine
origin evidence. Separate isolated native tests exercise the original B3 method.
"""
import copy
from dataclasses import replace
import hashlib
import json
import pickle
import runpy
import sqlite3
import struct
from types import SimpleNamespace
from uuid import UUID

import pytest
from src.data.contracts.controlled_h1_execution import (
    ControlledH1ExecutionRequest, ControlledH1ExecutionResult, ExecutionOutcome,
)
from src.data.contracts.successful_b3_producer import (
    ProducerLimits, SuccessfulB3Receipt, SuccessfulB3ReceiptDeclaration, result_set_identity,
)
from src.providers.owned_execution_provider import OwnedExecutionProvider
from src.services.controlled_execution_authority import ControlledExecutionAuthority
from src.services.controlled_h1_execution_service import ControlledH1ExecutionService
from src.services.successful_b3_producer_service import ProducerEvidenceError, SuccessfulB3ProducerService
import src.services.successful_b3_producer_service as implementation


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    cases = runpy.run_path('tests/runtime/test_owned_executable_inputs.py')
    generator = cases['build'].__wrapped__(tmp_path_factory)
    build = next(generator)
    try:
        s = build()
        output = s['prep'].prepare(s['result'].candidate, s['request'])
        assert output.status == 'PREPARED_INPUTS'
        yield s, output.inputs
    finally:
        with pytest.raises(StopIteration):
            next(generator)


@pytest.fixture
def make(prepared, monkeypatch):
    fixture, original_inputs = prepared
    contexts = {}

    def fake_b3(service, reference, native, inputs, request, *, capability, cancelled):
        s = contexts[id(service)]
        s.calls += 1
        cancelled()
        _, grant, _ = s.authority._validate(capability, reference, native, inputs,
            request, s.control['wall'], None, True)
        if s.control['mode'] == 'exception':
            raise RuntimeError('PRIVATE_PRODUCER_FAILURE')
        if s.control['mode'] != 'success':
            status = s.control['mode']
            return ExecutionOutcome(status=status, reason=('NATIVE_DEPENDENCY_UNAVAILABLE' if status == 'UNAVAILABLE'
                                                          else 'INVALID_UNAUTHORIZED_OR_INTERRUPTED'))
        values = s.control.get('predictions', (10., 20., 30., 40.))
        result = ControlledH1ExecutionResult(request=request, grant=grant, adapter='owned_xgboost_3_2_0_h1_v1',
            predictions=values, prediction_sha256=hashlib.sha256(struct.pack('<4f', *values)).hexdigest())
        outcome = ExecutionOutcome(status='EXECUTED', reason='CONTROLLED_VALIDATION_ONLY', result=result)
        s.returned.append(outcome)
        return outcome

    monkeypatch.setattr(implementation, '_EXECUTE', fake_b3)  # test-only protected-host substitution

    def create(**kwargs):
        control = dict(wall=100., elapsed=0., owner=True, mode='success')
        reference = object()
        native = object()  # deliberately NOT a genuine B1 native envelope
        inputs = type(original_inputs)(original_inputs.binding, original_inputs.output)
        request = ControlledH1ExecutionRequest(family='xgboost', candidate_id=fixture['result'].metadata.candidate_id,
            artifact_id='a'*64, payload_sha256='b'*64, inputs=inputs.binding)
        provider = object.__new__(OwnedExecutionProvider)
        provider._self = provider
        record = object()
        evidence = SimpleNamespace(record=record, output=inputs.output)
        def require(ref, nat, inp, req):
            if not control['owner'] or (ref is not reference or nat is not native or inp is not inputs or req != request):
                raise ValueError('unit-only evidence mismatch')
            return evidence
        provider.require = require  # explicit unit fake; no genuine provenance claim
        execution_operator = object()
        authority = ControlledExecutionAuthority(provider, operator=execution_operator, wall_clock=lambda: control['wall'])
        service = ControlledH1ExecutionService(authority, elapsed_clock=lambda: control['elapsed'])
        operator = object()
        producer = SuccessfulB3ProducerService(service, operator=operator,
            wall_clock=lambda: control['wall'], elapsed_clock=lambda: control['elapsed'], **kwargs)
        cap = authority.issue(execution_operator, reference, native, inputs, request)
        s = SimpleNamespace(control=control, reference=reference, native=native, inputs=inputs, request=request,
            provider=provider, authority=authority, service=service, operator=operator, execution_operator=execution_operator,
            producer=producer, cap=cap, returned=[], calls=0)
        contexts[id(service)] = s
        return s
    return create


def issue(s, **kwargs):
    return s.producer.produce(s.operator, s.reference, s.native, s.inputs, s.request,
                             capability=s.cap, deadline=130., **kwargs)


def verify(s, handle, **kwargs):
    return s.producer.verify(handle, s.reference, s.native, s.inputs, s.request,
                             deadline=200., **kwargs)


def test_contract_stub_retains_exact_original_return_and_detaches_public_views(make):
    s = make(); handle = issue(s)
    record = s.producer._issued[id(handle)]
    assert record.outcome is s.returned[0] and record.result is s.returned[0].result
    a = verify(s, handle); b = verify(s, handle)
    assert a is not b and a.execution is not record.result
    assert a.canonical_bytes == b.canonical_bytes and a.receipt_id == b.receipt_id
    assert a.result_set_sha256 == b.result_set_sha256 and a.shape == (4,)
    assert all(r.float32_le == struct.pack('<f', v).hex() for r, v in zip(a.rows, a.execution.predictions))
    object.__setattr__(a.execution, 'prediction_sha256', 'f'*64)
    assert verify(s, handle).canonical_bytes == b.canonical_bytes
    assert not b.write_permission and not b.persistable and not b.execution_permission


@pytest.mark.parametrize('mode', ['DENIED', 'UNAVAILABLE', 'exception'])
def test_contract_stub_failed_consumed_attempt_never_issues(make, mode):
    s = make(); s.control['mode'] = mode
    with pytest.raises(ProducerEvidenceError): issue(s)
    assert not s.producer._issued and s.authority._grants[id(s.cap)].state == 'CONSUMED'
    s.control['mode'] = 'success'
    with pytest.raises(ProducerEvidenceError): issue(s)
    assert not s.producer._issued


@pytest.mark.parametrize('fault', ['constructed', 'object', 'declaration', 'result', 'pickle', 'copy'])
def test_contract_stub_forged_copied_and_detached_handles(make, fault):
    s = make(); h = issue(s)
    if fault == 'copy':
        with pytest.raises(TypeError): copy.copy(h)
        return
    if fault == 'pickle':
        with pytest.raises(TypeError): pickle.dumps(h)
        return
    value = {'constructed': SuccessfulB3Receipt(), 'object': object(),
             'declaration': verify(s, h), 'result': s.returned[0].result}[fault]
    with pytest.raises(ProducerEvidenceError): verify(s, value)


def test_contract_stub_equal_foreign_result_is_not_origin(make):
    a = make(); ha = issue(a); b = make(); hb = issue(b)
    assert a.returned[0].result == b.returned[0].result and a.returned[0].result is not b.returned[0].result
    with pytest.raises(ProducerEvidenceError): verify(a, hb)
    with pytest.raises(ProducerEvidenceError): verify(b, ha)
    object.__setattr__(a.returned[0], 'result', b.returned[0].result)
    with pytest.raises(ProducerEvidenceError): verify(a, ha)


@pytest.mark.parametrize('field', ['reference', 'native', 'inputs'])
def test_contract_stub_original_envelope_and_owner_identity(make, field):
    s = make(); h = issue(s)
    setattr(s, field, object())
    with pytest.raises(ProducerEvidenceError): verify(s, h)


@pytest.mark.parametrize('field', ['candidate', 'tenant', 'series', 'snapshot', 'origin', 'publication', 'family'])
def test_contract_stub_exact_expected_scope(make, field):
    s = make(); h = issue(s); r = s.request; binding = r.inputs; request = binding.request; sr = request.snapshot_request
    if field == 'candidate': r = r.model_copy(update={'candidate_id': 'f'*64})
    elif field == 'family': r = r.model_copy(update={'family': 'lightgbm'})
    elif field == 'snapshot': binding = binding.model_copy(update={'request': request.model_copy(update={'snapshot_id': 'f'*64})})
    else:
        if field == 'tenant': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'reference': replace(sr.dataset.reference, organization_id=UUID(int=999))})})
        elif field == 'series': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'series_ids': sr.dataset.series_ids[:1]})})
        elif field == 'publication': sr = sr.model_copy(update={'dataset': sr.dataset.model_copy(update={'admission_digest': 'f'*64})})
        else: sr = sr.model_copy(update={'forecast_origin': sr.forecast_origin.replace(year=2030)})
        binding = binding.model_copy(update={'request': request.model_copy(update={'snapshot_request': sr})})
    if field not in ('candidate', 'family'): r = r.model_copy(update={'inputs': binding})
    s.request = r
    with pytest.raises(ProducerEvidenceError): verify(s, h)


@pytest.mark.parametrize('field', ['rows', 'bits', 'digest', 'shape', 'dtype', 'positive_permission'])
def test_contract_stub_changed_encodings_reject(make, field):
    s = make(); h = issue(s); record = s.producer._issued[id(h)]
    data = json.loads(record.declaration_json)
    if field == 'rows': data['rows'].reverse()
    elif field == 'bits': data['rows'][0]['float32_le'] = '00000000'
    elif field == 'digest': data['result_set_sha256'] = 'f'*64
    elif field == 'shape': data['shape'] = [3]
    elif field == 'dtype': data['physical_dtype'] = 'float64'
    else: data['write_permission'] = True
    object.__setattr__(record, 'declaration_json', json.dumps(data))
    with pytest.raises(ProducerEvidenceError): verify(s, h)


@pytest.mark.parametrize('fault', ['owner', 'receipt', 'execution_grant', 'issuer', 'expiry', 'clock_rollback'])
def test_contract_stub_live_revocation_and_expiry(make, fault):
    s = make(); h = issue(s, lifetime_seconds=20)
    if fault == 'owner': s.control['owner'] = False
    elif fault == 'receipt': assert s.producer.revoke(s.operator, h)
    elif fault == 'execution_grant': s.authority.revoke(s.execution_operator, s.cap)
    elif fault == 'issuer': s.producer.invalidate(s.operator)
    elif fault == 'expiry': s.control['wall'] = 120.
    else: s.control['wall'] = 99.
    with pytest.raises(ProducerEvidenceError): verify(s, h)


def test_contract_stub_expired_execution_window_is_not_receipt_reexecution(make):
    s = make(); h = issue(s, lifetime_seconds=60); s.control['wall'] = 140.
    assert verify(s, h).expires_at == 160.
    with pytest.raises(ProducerEvidenceError): issue(s)


@pytest.mark.parametrize('operation', ['produce', 'verify'])
@pytest.mark.parametrize('edge', ['cancel', 'wall', 'elapsed'])
def test_contract_stub_actual_final_callback_revocation(make, operation, edge):
    def run(s, counts, action=None):
        h = issue(s) if operation == 'verify' else None
        original_wall = s.producer._wall; original_elapsed = s.producer._elapsed
        def callback(key, value):
            counts[key] += 1
            if action is not None and key == edge and counts[key] == action[0]:
                if operation == 'verify': s.producer.revoke(s.operator, h)
                else: s.producer.invalidate(s.operator)
            return value
        s.producer._wall = lambda: callback('wall', original_wall())
        s.producer._elapsed = lambda: callback('elapsed', original_elapsed())
        cancel = lambda: callback('cancel', False)
        return verify(s, h, cancelled=cancel) if h else issue(s, cancelled=cancel)
    counts = dict(cancel=0, wall=0, elapsed=0); run(make(), counts)
    total = counts[edge]; assert total >= 2
    observed = dict(cancel=0, wall=0, elapsed=0); s = make()
    with pytest.raises(ProducerEvidenceError): run(s, observed, (total,))
    assert observed[edge] == total
    if operation == 'produce': assert not s.producer._issued


@pytest.mark.parametrize('operation', ['produce', 'verify'])
def test_contract_stub_cancellation_and_exact_deadline(make, operation):
    s = make(); h = issue(s) if operation == 'verify' else None
    fn = (lambda **kw: verify(s, h, **kw)) if h else (lambda **kw: issue(s, **kw))
    with pytest.raises(ProducerEvidenceError): fn(cancelled=lambda: True)
    s.control['wall'] = 200. if h else 130.
    with pytest.raises(ProducerEvidenceError): fn()


def test_contract_stub_bounded_registry_no_implicit_eviction(make):
    s = make(limits=ProducerLimits(max_receipts=1)); h = issue(s)
    s.cap = s.authority.issue(s.execution_operator, s.reference, s.native, s.inputs, s.request)
    with pytest.raises(ProducerEvidenceError): issue(s)
    assert s.calls == 1 and s.authority._grants[id(s.cap)].state == 'ISSUED'
    assert verify(s, h)


def test_contract_stub_no_result_argument_or_receipt_based_write_authority(make):
    s = make(); h = issue(s)
    with pytest.raises(TypeError): s.producer.produce(result=s.returned[0])
    assert not hasattr(h, 'result') and not hasattr(h, 'write_permission')
    assert not verify(s, h).execution.persistable
    assert not hasattr(s.producer, 'persist')


def test_contract_stub_no_storage_or_second_execution_during_verification(make, monkeypatch):
    s = make(); h = issue(s); before = s.calls
    def no(*a, **kw): raise AssertionError('unexpected operation')
    monkeypatch.setattr(sqlite3, 'connect', no)
    monkeypatch.setattr(implementation, '_EXECUTE', no)
    from src.execution.controlled_h1_adapters import XGBoostH1Adapter
    monkeypatch.setattr(XGBoostH1Adapter, 'load', no)
    monkeypatch.setattr(XGBoostH1Adapter, 'predict', no)
    assert verify(s, h) and s.calls == before


def test_contract_stub_private_errors_and_hostile_actor_are_sanitized(make):
    s = make(); s.control['mode'] = 'exception'
    with pytest.raises(ProducerEvidenceError) as caught: issue(s)
    assert 'PRIVATE' not in str(caught.value) and caught.value.__context__ is None
    class Hostile:
        def __str__(self): raise AssertionError('hostile coercion')
        def __repr__(self): raise AssertionError('hostile repr')
    with pytest.raises(ProducerEvidenceError):
        s.producer.produce(Hostile(), s.reference, s.native, s.inputs, s.request, capability=s.cap, deadline=130.)


def test_contract_stub_serialization_strict_and_deterministic(make):
    s = make(); h = issue(s); d = verify(s, h)
    assert SuccessfulB3ReceiptDeclaration.model_validate_json(d.canonical_json).canonical_bytes == d.canonical_bytes
    with pytest.raises(ValueError): SuccessfulB3ReceiptDeclaration.model_validate_json(d.canonical_json.replace('"version":"1.0"', '"version":"1.0","version":"1.0"', 1))
    with pytest.raises(ValueError): d.expires_at = 999.


def test_contract_stub_coherently_rehashed_result_substitution_still_rejects(make):
    s = make(); h = issue(s); d = verify(s, h)
    values = tuple(reversed(d.execution.predictions))
    execution = d.execution.model_copy(update={'predictions': values,
        'prediction_sha256': hashlib.sha256(struct.pack('<4f', *values)).hexdigest()})
    rows = tuple(reversed(d.rows))
    changed = d.model_copy(update={'execution': execution, 'rows': rows,
        'result_set_sha256': result_set_identity(execution, rows)})
    validated = SuccessfulB3ReceiptDeclaration.model_validate_json(changed.canonical_json)
    assert validated.result_set_sha256 != d.result_set_sha256
    object.__setattr__(s.producer._issued[id(h)], 'declaration_json', validated.canonical_json)
    with pytest.raises(ProducerEvidenceError): verify(s, h)


def test_contract_stub_mutation_of_original_result_is_detected(make):
    s = make(); h = issue(s)
    object.__setattr__(s.returned[0].result, 'predictions', (40., 30., 20., 10.))
    with pytest.raises(ProducerEvidenceError): verify(s, h)


@pytest.mark.parametrize('field', ['execution_permission', 'write_permission', 'persistable'])
def test_contract_stub_no_positive_permission_construction(make, field):
    s = make(); h = issue(s); data = json.loads(verify(s, h).canonical_json); data[field] = True
    with pytest.raises(ValueError): SuccessfulB3ReceiptDeclaration.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), 'PRIVATE_CLOCK'])
def test_contract_stub_invalid_clock_fails_closed(make, value):
    s = make(); s.producer._wall = lambda: value
    with pytest.raises(ProducerEvidenceError): issue(s)
    assert not s.producer._issued and s.calls == 0


def test_contract_stub_elapsed_limit_exact_boundary(make):
    s = make(limits=ProducerLimits(max_elapsed_seconds=1)); ticks = iter([0., 1.])
    s.producer._elapsed = lambda: next(ticks)
    with pytest.raises(ProducerEvidenceError): issue(s)
    assert not s.producer._issued


def test_contract_stub_copied_services_and_operator_labels_not_authority(make):
    s = make(); h = issue(s)
    for operator in ('operator', object(), None):
        with pytest.raises(ProducerEvidenceError): s.producer.revoke(operator, h)
    with pytest.raises(ProducerEvidenceError):
        SuccessfulB3ProducerService(copy.copy(s.service), operator=object())
    with pytest.raises(ProducerEvidenceError):
        copy.copy(s.producer).verify(h, s.reference, s.native, s.inputs, s.request, deadline=130.)
    assert verify(s, h)


def test_contract_stub_reentrancy_rejects_without_deadlock(make):
    s = make(); attempted = []
    def callback():
        with pytest.raises(ProducerEvidenceError): issue(s)
        attempted.append(True)
        return False
    h = issue(s, cancelled=callback)
    assert attempted and verify(s, h)


def test_contract_stub_preserves_signed_zero_and_float32_bits(make):
    s = make(); s.control['predictions'] = (-0., 0., 1.5, -2.)
    h = issue(s); d = verify(s, h)
    assert d.rows[0].float32_le == '00000080' and d.rows[1].float32_le == '00000000'
    assert SuccessfulB3ReceiptDeclaration.model_validate_json(d.canonical_json).rows == d.rows


@pytest.mark.parametrize('operation', ['produce', 'verify'])
@pytest.mark.parametrize('edge', ['cancel', 'deadline', 'elapsed'])
def test_contract_stub_actual_terminal_interruption_and_expiry(make, operation, edge):
    key = 'wall' if edge == 'deadline' else edge
    def run(s, target=None):
        h = issue(s, lifetime_seconds=20) if operation == 'verify' else None
        counts = dict(cancel=0, wall=0, elapsed=0)
        wall = s.producer._wall; elapsed = s.producer._elapsed
        def poll(k, value):
            counts[k] += 1
            if k == key and target is not None and counts[k] == target:
                return True if edge == 'cancel' else (130. if operation == 'produce' else 120.) if edge == 'deadline' else 60.
            return value
        s.producer._wall = lambda: poll('wall', wall())
        s.producer._elapsed = lambda: poll('elapsed', elapsed())
        try:
            if h: verify(s, h, cancelled=lambda: poll('cancel', False))
            else: issue(s, cancelled=lambda: poll('cancel', False))
        finally: s.observed = counts
    baseline = make(); run(baseline)
    actual = make()
    with pytest.raises(ProducerEvidenceError): run(actual, baseline.observed[key])
    assert actual.observed[key] == baseline.observed[key]
    if operation == 'produce': assert not actual.producer._issued


@pytest.mark.parametrize('fault', ['missing_fields', 'hostile_limits'])
def test_contract_stub_invalid_construction_sanitized(make, fault):
    s = make()
    service = object.__new__(ControlledH1ExecutionService) if fault == 'missing_fields' else s.service
    class Hostile:
        @property
        def canonical_json(self): raise AssertionError('PRIVATE_LIMITS')
    with pytest.raises(ProducerEvidenceError) as caught:
        SuccessfulB3ProducerService(service, operator=object(), limits=Hostile() if fault == 'hostile_limits' else None)
    assert caught.value.__context__ is None and 'PRIVATE' not in str(caught.value)
