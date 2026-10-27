"""B1: genuine native fits, only inside the documented isolated offline runner."""
from copy import copy
from dataclasses import replace
import importlib.abc
import json
import runpy
import sys
import os
import socket
from pathlib import Path
import traceback
from unittest.mock import Mock
import numpy as np
import pytest
from src.data.contracts.native_xgboost_roundtrip import NativeRoundTripLimits, NativeArtifactEnvelope, NativeXGBoostManifest
from src.data.contracts.training_adapter import TrainingSpecification
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService, CandidateReference
from src.services.owned_xgboost_roundtrip_service import OwnedXGBoostRoundTripService, NativeRoundTripError
from src.training.canonical_adapters import NativeBackend


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    assert os.environ.get("B1_ISOLATED_NATIVE_TEST") == "1", "use tests/native/run_b1.py"
    assert os.getuid() != 0 and socket.if_nameindex() == [(1, "lo")]
    assert int(Path("/sys/fs/cgroup/memory.max").read_text()) <= 1073741824
    # Reuse actual FINALIZED canonical publications, providers and M3/M4/M5
    # composition. The inherited test backend is not invoked or retained.
    factory = runpy.run_path('tests/training/test_canonical_adapter_training.py')['setup'].__wrapped__(tmp_path_factory.mktemp('genuine-b1'))
    b = factory()
    b['trainer'] = CanonicalAdapterTrainingService(b['tabular'], b['recurrent'], b['transformer'], authorize_training=lambda _: True)
    b['spec'] = TrainingSpecification(family='xgboost', steps=4, input_dtype='float32')
    b['outcome'] = b['trainer'].train(b['spec'], plan=b['plan'], feature_configuration=b['configuration'])
    assert b['outcome'].status == 'SUCCESS', b['outcome'].status
    assert b['outcome'].metadata.backend == 'native_framework'
    prepared = b['tabular'].prepare(b['plan'], b['configuration'], dtype='float32')
    b['validation'] = next(o for o in prepared.outputs if o.representation.binding.partition == 'validation')
    assert not any(c[0] == 'fit' for c in b['calls'])
    yield b
    b['trainer'].discard(b['outcome'].candidate)


@pytest.fixture
def context(genuine):
    calls = []
    def approve(metadata, stage, artifact_id, validation_id):
        calls.append(stage)
        assert metadata == genuine['outcome'].metadata
        if validation_id is not None:
            assert validation_id == genuine['validation'].output_id
        return True
    service = OwnedXGBoostRoundTripService(genuine['trainer'], authorize=approve)
    return genuine, service, calls


def test_actual_native_export_reload_bitwise_parity(context, monkeypatch):
    b, service, calls = context
    import xgboost as xgb
    assert xgb.__version__ == '3.2.0'
    original = b['trainer']._candidates[id(b['outcome'].candidate)][3].get_booster()
    old = bytes(original.save_raw(raw_format='ubj'))
    envelope = service.export(b['outcome'].candidate)
    assert bytes(original.save_raw(raw_format='ubj')) == old
    assert envelope.manifest.training.candidate_id == b['outcome'].metadata.candidate_id
    assert envelope.manifest.feature_schema == b['validation'].state.feature_schema
    assert envelope.manifest.native_input_dtype == 'float32'
    # Native loader is real; preprocessing and training are forbidden now.
    trap = Mock(side_effect=AssertionError('no fitting or preparation'))
    monkeypatch.setattr(b['tabular'], 'prepare', trap)
    monkeypatch.setattr(NativeBackend, 'fit', trap)
    report = service.verify(b['outcome'].candidate, envelope, b['validation'])
    assert report.original_prediction_sha256 == report.reloaded_prediction_sha256
    assert report.comparison == 'bitwise_float32_v1'
    assert not report.runtime_permission and not report.production_permission
    print('B1_NATIVE_EVIDENCE', json.dumps({'xgboost': xgb.__version__, 'payload_size': len(envelope.payload),
        'payload_sha256': envelope.manifest.payload_sha256, 'artifact_id': envelope.manifest.artifact_id,
        'candidate_id': report.candidate_id, 'validation_rows': report.validation_rows,
        'original_prediction_sha256': report.original_prediction_sha256,
        'reloaded_prediction_sha256': report.reloaded_prediction_sha256, 'comparison': report.comparison}, sort_keys=True))
    assert calls == ['export', 'export_return', 'load_validation', 'compare_validation', 'verify_return']
    assert service.verify(b['outcome'].candidate, envelope, b['validation']).canonical_bytes == report.canonical_bytes
    trap.assert_not_called()


def test_independent_native_api_reloads_genuine_payload(context):
    import xgboost as xgb
    from src.data.contracts.model_candidate import strict_float32
    b, service, _ = context
    envelope = service.export(b['outcome'].candidate)
    loaded = xgb.Booster(params={'nthread': 1})
    loaded.load_model(bytearray(envelope.payload))
    x = strict_float32(b['validation'].values)
    actual = loaded.predict(xgb.DMatrix(x, feature_names=[f.name for f in envelope.manifest.feature_schema.features], nthread=1))
    original = b['trainer']._candidates[id(b['outcome'].candidate)][3].predict(x)
    assert actual.dtype == np.float32 and actual.tobytes() == original.tobytes()


@pytest.mark.parametrize('bad', ['copy', 'reconstructed', 'bytes', 'alias', 'foreign'])
def test_unowned_references_reject_before_framework(context, monkeypatch, bad):
    b, service, _ = context
    ref = b['outcome'].candidate
    value = {'copy': copy(ref), 'reconstructed': CandidateReference(), 'bytes': b'ubj', 'alias': 'production', 'foreign': object()}[bad]
    monkeypatch.setattr('src.services.owned_xgboost_roundtrip_service._framework', Mock(side_effect=AssertionError('loader touched')))
    with pytest.raises(NativeRoundTripError): service.export(value)


@pytest.mark.parametrize('fault', ['copy', 'payload', 'size', 'digest', 'candidate', 'family', 'format', 'version', 'schema'])
def test_tampering_rejects_before_native_parser(context, monkeypatch, fault):
    b, service, _ = context
    envelope = service.export(b['outcome'].candidate)
    m = envelope.manifest
    if fault == 'copy':
        with pytest.raises(TypeError): copy(envelope)
        bad = NativeArtifactEnvelope(envelope.manifest, envelope.payload)
    elif fault == 'payload': bad = replace(envelope, payload=envelope.payload[:-1]+b'x')
    else:
        fields = {'size': ('payload_size', 1), 'digest': ('payload_sha256', 'f'*64), 'candidate': ('training', m.training.model_copy(update={'input_digest': 'f'*64})),
                  'family': ('training', m.training.model_copy(update={'specification': m.training.specification.model_copy(update={'family': 'lightgbm'})})),
                  'format': ('format', 'pickle'), 'version': ('framework_version', '3.1.0'), 'schema': ('feature_schema', m.feature_schema.model_copy(update={'features': tuple(reversed(m.feature_schema.features))}))}
        key, value = fields[fault]
        bad = replace(envelope, manifest=m.model_copy(update={key: value}))
    spy = Mock(side_effect=AssertionError('unowned bytes reached native parser'))
    monkeypatch.setattr('src.services.owned_xgboost_roundtrip_service._framework', spy)
    with pytest.raises(NativeRoundTripError): service.verify(b['outcome'].candidate, bad, b['validation'])
    spy.assert_not_called()
    # Original issued immutable object remains unchanged and independently owned.
    assert envelope.manifest == m


@pytest.mark.parametrize('fault', ['dtype', 'float64_schema', 'order', 'state', 'partition', 'family'])
def test_validation_substitutions_reject(context, fault):
    b, service, _ = context
    envelope = service.export(b['outcome'].candidate)
    output = b['validation']; r = output.representation
    if fault == 'dtype': output = output.model_copy(update={'values': tuple(tuple(True for _ in row) for row in output.values)})
    elif fault == 'float64_schema':
        features = tuple(f.model_copy(update={'dtype': 'float64', 'numeric_policy': 'strict_exact_float64_v1'}) if f.dtype == 'float32' else f for f in output.state.feature_schema.features)
        schema = output.state.feature_schema.model_copy(update={'features': features})
        output = output.model_copy(update={'state': output.state.model_copy(update={'feature_schema': schema}), 'representation': r.model_copy(update={'feature_schema': schema})})
    elif fault == 'order': output = output.model_copy(update={'representation': r.model_copy(update={'feature_schema': r.feature_schema.model_copy(update={'features': tuple(reversed(r.feature_schema.features))})})})
    elif fault == 'state': output = output.model_copy(update={'state': output.state.model_copy(update={'fold_id': 'foreign'})})
    elif fault == 'partition': output = output.model_copy(update={'representation': r.model_copy(update={'binding': r.binding.model_copy(update={'partition': 'test'})})})
    else: output = output.model_copy(update={'model_family': 'lightgbm'})
    with pytest.raises(NativeRoundTripError): service.verify(b['outcome'].candidate, envelope, output)


@pytest.mark.parametrize('fault', ['missing', 'version'])
def test_missing_or_wrong_framework_fails_closed(context, monkeypatch, fault):
    from types import SimpleNamespace
    b, service, _ = context
    if fault == 'missing': monkeypatch.setattr('src.services.owned_xgboost_roundtrip_service.importlib.import_module', Mock(side_effect=ModuleNotFoundError('PRIVATE')))
    else: monkeypatch.setattr('src.services.owned_xgboost_roundtrip_service.importlib.import_module', lambda _: SimpleNamespace(__version__='3.1.0'))
    with pytest.raises(NativeRoundTripError) as caught: service.export(b['outcome'].candidate)
    assert 'PRIVATE' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('fault', ['cancel', 'deadline', 'size', 'rows', 'denied', 'revoke_export', 'revoke_return'])
def test_resource_denial_and_terminal_revocation(genuine, fault):
    b = genuine; state = {'elapsed': 0.}
    ref = b['outcome'].candidate
    original = b['trainer']._candidates[id(ref)]
    def authorize(metadata, stage, aid, vid):
        if fault == 'deadline': state['elapsed'] = 1.
        if fault == 'revoke_export' and stage == 'export_return': b['trainer'].discard(ref)
        if fault == 'revoke_return' and stage == 'verify_return': b['trainer'].discard(ref)
        return fault != 'denied'
    limits = NativeRoundTripLimits(max_elapsed_seconds=1, max_artifact_bytes=1 if fault == 'size' else 1048576,
                                   max_rows=1 if fault == 'rows' else 32)
    service = OwnedXGBoostRoundTripService(b['trainer'], authorize=authorize, limits=limits, elapsed_clock=lambda: state['elapsed'])
    try:
        with pytest.raises(NativeRoundTripError):
            envelope = service.export(ref, cancelled=lambda: fault == 'cancel')
            service.verify(ref, envelope, b['validation'])
    finally:
        # Fixture restoration only, not a supported reactivation API.
        b['trainer']._candidates[id(ref)] = original


def test_stub_owner_rejected(tmp_path):
    factory = runpy.run_path('tests/training/test_canonical_adapter_training.py')['setup'].__wrapped__(tmp_path)
    b = factory(); result = b['run']()
    assert result.metadata.backend == 'test_stub'
    with pytest.raises(NativeRoundTripError): OwnedXGBoostRoundTripService(b['trainer'], authorize=lambda *_: True)


def test_m7_native_candidate_stays_unavailable(genuine, tmp_path):
    from src.services.candidate_packaging_service import CandidatePackagingService
    from src.providers.candidate_package_provider import CandidatePackageProvider
    from src.repositories.candidate_package_repository import CandidatePackageRepository
    provider = CandidatePackageProvider(CandidatePackageRepository(str(tmp_path)))
    packaging = CandidatePackagingService(genuine['trainer'], provider, authorize_packaging=lambda _: True)
    assert packaging.package(genuine['outcome'].candidate, test_only=True).status == 'UNAVAILABLE'


def test_operational_modules_are_not_imported(context):
    b, service, _ = context
    forbidden = ('mlflow', 'src.inference.predictor', 'src.services.canonical_h1_predictor_service',
                 'src.services.canonical_forecast_persistence_inspection_service', 'src.ml.production_resolver',
                 'src.deployment.production_artifact_sync', 'src.database.db', 'src.repositories.forecast_repository')
    class Trap(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == n or fullname.startswith(n+'.') for n in forbidden):
                raise AssertionError('operational import')
    guard = Trap(); sys.meta_path.insert(0, guard)
    try:
        assert not any(n in sys.modules for n in forbidden)
        envelope = service.export(b['outcome'].candidate)
        service.verify(b['outcome'].candidate, envelope, b['validation'])
    finally: sys.meta_path.remove(guard)


@pytest.mark.parametrize('fault', ['parity', 'nonfinite', 'dtype', 'shape', 'loaded_order'])
def test_native_incompatibility_never_returns_report(context, monkeypatch, fault):
    import xgboost as xgb
    b, service, _ = context
    ref = b['outcome'].candidate
    envelope = service.export(ref)
    model = b['trainer']._candidates[id(ref)][3]
    original = model.predict
    if fault == 'loaded_order':
        load = xgb.Booster.load_model
        def wrong(self, *args, **kw):
            load(self, *args, **kw)
            self.feature_names = list(reversed(self.feature_names))
        monkeypatch.setattr(xgb.Booster, 'load_model', wrong)
    else:
        def wrong(x):
            values = original(x)
            if fault == 'parity': return values+np.float32(1)
            if fault == 'nonfinite': return np.full(values.shape, np.nan, dtype=np.float32)
            if fault == 'dtype': return values.astype('float64')
            return values[:, None]
        monkeypatch.setattr(model, 'predict', wrong)
    with pytest.raises(NativeRoundTripError): service.verify(ref, envelope, b['validation'])


@pytest.mark.parametrize('edge', ['cancel', 'clock'])
def test_terminal_callback_discard_rejects_without_result(genuine, edge):
    b = genuine; ref = b['outcome'].candidate
    original = b['trainer']._candidates[id(ref)]
    stages = {'final': False}
    def authorize(metadata, stage, aid, vid):
        stages['final'] = stage == 'verify_return'
        return True
    def revoke():
        if stages['final']: b['trainer'].discard(ref)
    def clock():
        if edge == 'clock': revoke()
        return 0.
    def cancelled():
        if edge == 'cancel': revoke()
        return False
    service = OwnedXGBoostRoundTripService(b['trainer'], authorize=authorize, elapsed_clock=clock)
    envelope = service.export(ref)
    try:
        with pytest.raises(NativeRoundTripError): service.verify(ref, envelope, b['validation'], cancelled=cancelled)
    finally: b['trainer']._candidates[id(ref)] = original


def test_issued_payload_mutation_is_rejected_before_loader(context, monkeypatch):
    b, service, _ = context
    envelope = service.export(b['outcome'].candidate)
    payload = envelope.payload
    trap = Mock(side_effect=AssertionError('tampered native payload loaded'))
    monkeypatch.setattr('src.services.owned_xgboost_roundtrip_service._framework', trap)
    try:
        object.__setattr__(envelope, 'payload', payload[:-1]+b'x')
        with pytest.raises(NativeRoundTripError): service.verify(b['outcome'].candidate, envelope, b['validation'])
    finally: object.__setattr__(envelope, 'payload', payload)
    trap.assert_not_called()


def test_discarded_and_copied_owner_cannot_verify(context):
    b, service, _ = context; ref = b['outcome'].candidate
    envelope = service.export(ref)
    copied = OwnedXGBoostRoundTripService(copy(b['trainer']), authorize=lambda *_: True)
    with pytest.raises(NativeRoundTripError): copied.export(ref)
    record = b['trainer']._candidates[id(ref)]
    try:
        assert b['trainer'].discard(ref)
        with pytest.raises(NativeRoundTripError): service.verify(ref, envelope, b['validation'])
    finally: b['trainer']._candidates[id(ref)] = record


def test_different_genuinely_fitted_candidate_cannot_use_envelope(context):
    b, service, _ = context
    envelope = service.export(b['outcome'].candidate)
    other = b['trainer'].train(TrainingSpecification(family='xgboost', steps=4, seed=43),
        plan=b['plan'], feature_configuration=b['configuration'])
    assert other.status == 'SUCCESS' and other.metadata.backend == 'native_framework'
    assert other.metadata.candidate_id != b['outcome'].metadata.candidate_id
    try:
        with pytest.raises(NativeRoundTripError): service.verify(other.candidate, envelope, b['validation'])
    finally: b['trainer'].discard(other.candidate)


@pytest.mark.parametrize('fault', ['format', 'version', 'native_backend', 'family', 'frozen_state', 'attribute'])
def test_native_manifest_cannot_relabel_binding(context, fault):
    b, service, _ = context
    m = service.export(b['outcome'].candidate).manifest
    data = m.model_dump(mode='json')
    if fault == 'format': data['format'] = 'joblib'
    elif fault == 'version': data['framework_version'] = '2.0.0'
    elif fault == 'native_backend': data['training']['backend'] = 'test_stub'
    elif fault == 'family': data['training']['specification']['family'] = 'lightgbm'
    elif fault == 'frozen_state': data['frozen_state_id'] = 'e'*64
    else: data['native_attributes'] = []
    with pytest.raises(ValueError): NativeXGBoostManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('fault', ['terminal_cancel', 'terminal_deadline', 'callback_exception'])
def test_final_failure_returns_no_parity_report(genuine, fault):
    b = genuine; state = {'final': False, 'elapsed': 0.}
    def approve(metadata, stage, aid, vid):
        if stage == 'verify_return':
            state['final'] = True
            if fault == 'terminal_deadline': state['elapsed'] = 1.
            if fault == 'callback_exception': raise RuntimeError('PRIVATE_B1_CALLBACK')
        return True
    service = OwnedXGBoostRoundTripService(b['trainer'], authorize=approve,
        limits=NativeRoundTripLimits(max_elapsed_seconds=1), elapsed_clock=lambda: state['elapsed'])
    envelope = service.export(b['outcome'].candidate)
    with pytest.raises(NativeRoundTripError) as caught:
        service.verify(b['outcome'].candidate, envelope, b['validation'],
            cancelled=lambda: state['final'] and fault == 'terminal_cancel')
    assert 'PRIVATE_B1_CALLBACK' not in ''.join(traceback.format_exception(caught.value))
