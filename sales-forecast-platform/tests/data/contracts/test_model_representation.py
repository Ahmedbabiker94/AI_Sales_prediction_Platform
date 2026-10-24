"""M2 metadata validation only: no model, feature fitting or buffer conversion."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import UUID
import warnings

import pytest

from src.data.contracts.dataset import StoredDatasetReference, operational_json
from src.data.contracts.features import FeatureConfiguration, FeatureState
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_representation import (
    TabularRepresentation, RecurrentRepresentation, TransformerRepresentation,
    InputFeature, WindowDeclaration, RepresentationBinding, validate_representation,
    require_compatible,
)
from src.data.contracts.temporal_preparation import TemporalDatasetBinding, TemporalFold, TemporalPreparationPlan
from src.data.contracts.backtesting import identity
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.feature_schema_compatibility import describe_feature_schema


def clone(value):
    # Existing immutable canonical models contain mappingproxy fields.
    if type(value) is dict: return {k: clone(v) for k, v in value.items()}
    if type(value) is tuple: return tuple(clone(v) for v in value)
    if type(value) is list: return [clone(v) for v in value]
    return value


def day(value):
    return datetime(2020, 2, 3, tzinfo=timezone.utc) + timedelta(weeks=value)


@pytest.fixture
def declarations():
    spec = importlib.util.spec_from_file_location('m2_feature_fixture', 'tests/features/conftest.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    cfg = fixture.feature_config().model_dump()
    cfg.update(canonical_inputs=('value', 'period_start', 'period_end', 'location', 'promotion'),
               ordered_outputs=('location', 'lag1', 'promotion'),
               calculations=(dict(output='lag1', operation='lag', inputs=('value',), parameters={'offset': 1}),),
               missing_value_behavior={'location': 'reject', 'lag1': 'reject', 'promotion': 'reject'},
               category_encoding={'location': 'ordinal'},
               output_dtypes={'location': 'int8', 'lag1': 'float64', 'promotion': 'float64'})
    cfg['execution'].update(dimension_fields=('location',), categories=(dict(field='location', unknown='reject', missing='reject'),),
        exogenous=(dict(field='promotion', availability='known_in_advance', missing='reject'),))
    cfg = FeatureConfiguration.model_validate(cfg)
    engine = CanonicalFeaturePipeline(cfg)
    # Construct honest declaration-only fitted-state metadata; never fit state.
    state = FeatureState(executor_version=engine.executor_version, configuration_id=cfg.configuration_id,
        configuration_version=cfg.version, configuration_digest=engine.digest, training_schema_digest='b'*64,
        training_partition_reference='original-training-partition', training_cutoff=day(0),
        organization_ids=(UUID(int=1),), vocabularies={'location': ('north', 'south')})
    schema = describe_feature_schema(cfg, state, forecast_config=ForecastConfig(forecast_frequency='weekly', forecast_horizon=1), forecast_origin=day(2))
    dataset = TemporalDatasetBinding(reference=StoredDatasetReference(UUID(int=1), UUID(int=2), UUID(int=3), 'a'*64),
        source_id=UUID(int=4), manifest_digest='a'*64, admission_digest='c'*64, mapping_id='mapping', mapping_version='1.0',
        mapping_digest='d'*64, quality_policy_id='SALES_GENERAL', quality_policy_version='1.0',
        series_definition_digest='e'*64, series_ids=(UUID(int=5),))
    fold = TemporalFold(fold_id='fold-1', training_origins=(day(0), day(1)), validation_origins=(day(2), day(3)),
        test_origins=(day(4),), calibration_start=day(-4), categorical_cutoff=day(0),
        model_fit_cutoff=day(2), assessment_cutoff=day(5))
    plan = TemporalPreparationPlan(plan_id='original-plan', datasets=(dataset,),
        configuration_id=cfg.configuration_id, configuration_version=cfg.version, configuration_digest=engine.digest,
        frequency='weekly', calendar='gregorian', timezone='UTC', period_anchor='start', purpose='production_training', folds=(fold,))
    binding = dict(plan=plan, fold_id='fold-1', partition='validation', canonical_schema=schema,
                   canonical_state=state, historical_revision='immutable-history-v1', historical_evidence_digest='f'*64)
    points = tuple((dict(plan_digest=plan.digest, dataset_version='a'*64, fold_id='fold-1', organization_id=UUID(int=1),
        series_id=UUID(int=5), metric='reported', unit='amount', currency='USD', frequency='weekly', timezone='UTC',
        dimensions=(('location', 'north'),), forecast_origin=day(n), horizon_step=1, target_period=day(n), target_period_end=day(n+1)),) for n in (2, 3))
    loc = dict(name='location', dtype='float32', numeric_policy='strict_exact_float32_v1',
               categorical_policy='frozen_vocabulary_v1', vocabulary_field='location')
    lag = dict(name='lag1', dtype='float32', numeric_policy='strict_exact_float32_v1')
    promotion = dict(name='promotion', dtype='float32', numeric_policy='strict_exact_float32_v1')
    common = dict(binding=binding, samples=points)
    tabular = dict(common, family='tabular', feature_schema=dict(features=(loc, lag)),
                   shape=(2, 2), missing_mask=((False, False), (False, False)), available_at=(day(2), day(3)))
    scaler = dict(policy='standard_v1', state_digest='1'*64, training_plan_digest=plan.digest, fold_id='fold-1',
                  training_origins_digest=identity([o.isoformat() for o in fold.training_origins]), training_cutoff=fold.model_fit_cutoff,
                  canonical_state_id=state.state_id, source_schema_id=schema.schema_id, organization_ids=state.organization_ids)
    recurrent = dict(common, family='recurrent', binding=dict(binding, scaler=scaler), feature_schema=dict(features=(lag,)),
        tensor_dtype='float32', window=dict(lookback=3, window=3, stride=1, past_offsets=(-3,-2,-1), padding='left'),
        shape=(2,3,1), masks=dict(lengths=(2,3), padding=((True,False,False),(False,False,False)),
        missing=(((True,),(False,),(False,)),((False,),(False,),(False,)))),
        available_at=((None,day(2),day(2)),(day(3),day(3),day(3))))
    transformer = dict(recurrent, family='transformer', static=dict(features=(loc,)), static_shape=(2,1),
        static_missing_mask=((False,),(False,)), static_available_at=(day(2),day(3)),
        known_future=dict(features=(promotion,)), known_future_shape=(2,1,1),
        known_future_masks=dict(lengths=(1,1), padding=((False,),(False,)), missing=(((False,),),((False,),))),
        known_future_available_at=((day(2),),(day(3),)), future_offsets=(0,),
        future_availability=(dict(field='promotion', rule='known_in_advance', evidence_identity='2'*64),))
    return {'tabular': tabular, 'recurrent': recurrent, 'transformer': transformer}


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
def test_valid_roundtrips_and_deterministic_identities(declarations, family):
    result = validate_representation(declarations[family])
    encoded = operational_json(result.model_dump(mode='json'))
    restored = validate_representation(encoded)
    assert restored == result and restored.representation_id == result.representation_id
    assert require_compatible(result, restored) == result.compatibility_id
    assert encoded == operational_json(restored.model_dump(mode='json'))
    assert result.status == 'DECLARATION_ONLY'


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
def test_immutable_detached_graph(declarations, family):
    source = declarations[family]
    result = validate_representation(source)
    before = result.representation_id
    source['binding']['historical_revision'] = 'changed'
    source['feature_schema']['features'][0]['name'] = 'changed'
    assert result.representation_id == before
    with pytest.raises(ValueError): result.version = '2.0'
    with pytest.raises(ValueError): result.binding.canonical_schema.configuration.configuration_id = 'changed'
    with pytest.raises(TypeError): result.binding.canonical_state.vocabularies['location'] = ('changed',)


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
@pytest.mark.parametrize('fault', ['version','axis','shape_bool','batch','feature_count','dtype','policy','state','schema','partition','origin','target','order','duplicate','unknown','plan'])
def test_incompatible_declarations_reject(declarations, family, fault):
    value = clone(declarations[family])
    if fault == 'version': value['version'] = '2.0'
    elif fault == 'axis': value['axes'] = ('features','batch')
    elif fault == 'shape_bool': value['shape'] = (True, *value['shape'][1:])
    elif fault == 'batch': value['shape'] = (1, *value['shape'][1:])
    elif fault == 'feature_count': value['shape'] = (*value['shape'][:-1],99)
    elif fault == 'dtype': value['feature_schema']['features'][0]['dtype'] = 'object'
    elif fault == 'policy': value['feature_schema']['features'][0]['numeric_policy'] = 'lossy_float32'
    elif fault == 'state': value['binding']['canonical_state'] = value['binding']['canonical_state'].model_copy(update={'configuration_id':'substituted'})
    elif fault == 'schema': value['binding']['canonical_schema'] = value['binding']['canonical_schema'].model_copy(update={'fitted_state_id':'9'*64})
    elif fault == 'partition': value['binding']['partition'] = 'training'
    elif fault == 'origin': value['samples'][0][0]['forecast_origin'] = day(4)
    elif fault == 'target': value['samples'][0][0]['target_period_end'] = day(4)
    elif fault == 'order': value['samples'] = tuple(reversed(value['samples']))
    elif fault == 'duplicate': value['samples'] = (value['samples'][0],)*2
    elif fault == 'unknown': value['unexpected'] = 'bad'
    else: value['samples'][0][0]['plan_digest'] = '9'*64
    with pytest.raises(EvaluationDeclarationError): validate_representation(value)


@pytest.mark.parametrize('family', ['recurrent','transformer'])
@pytest.mark.parametrize('fault', ['length','padding','missing_shape','missing_required','unmasked_padding','window','stride','positions','scaler_missing','scaler_partition','scaler_cutoff','scaler_origins','late','padded_release'])
def test_masks_window_scaler_and_availability(declarations, family, fault):
    value = clone(declarations[family])
    if fault == 'length': value['masks']['lengths'] = (0,3)
    elif fault == 'padding': value['masks']['padding'] = ((False,True,False),(False,False,False))
    elif fault == 'missing_shape': value['masks']['missing'] = (((True,True),(False,),(False,)),((False,),(False,),(False,)))
    elif fault == 'missing_required': value['masks']['missing'] = (((True,),(True,),(False,)),((False,),(False,),(False,)))
    elif fault == 'unmasked_padding': value['masks']['missing'] = (((False,),(False,),(False,)),((False,),(False,),(False,)))
    elif fault == 'window': value['window']['window'] = 2
    elif fault == 'stride': value['window']['stride'] = True
    elif fault == 'positions': value['window']['past_offsets'] = (-2,-1,0)
    elif fault == 'scaler_missing': value['binding']['scaler'] = None
    elif fault == 'scaler_partition': value['binding']['scaler']['fit_partition'] = 'validation'
    elif fault == 'scaler_cutoff': value['binding']['scaler']['training_cutoff'] = day(3)
    elif fault == 'scaler_origins': value['binding']['scaler']['training_origins_digest'] = '9'*64
    elif fault == 'late': value['available_at'] = ((None,day(3),day(2)),(day(3),day(3),day(3)))
    else: value['available_at'] = ((day(2),day(2),day(2)),(day(3),day(3),day(3)))
    with pytest.raises(EvaluationDeclarationError): validate_representation(value)


@pytest.mark.parametrize('number', [float('nan'),float('inf'),float('-inf'),1e39,0.1,True,1,'0.0'])
def test_invalid_or_lossy_fill_values_reject(declarations, number):
    value = clone(declarations['recurrent'])
    value['window']['fill_value'] = number
    with pytest.raises(EvaluationDeclarationError): validate_representation(value)


def test_signed_zero_and_explicit_masks(declarations):
    value = clone(declarations['recurrent'])
    value['window']['fill_value'] = -0.0
    result = validate_representation(value)
    assert '-0.0' in result.model_dump_json()
    assert result.representation_id != validate_representation(declarations['recurrent']).representation_id


@pytest.mark.parametrize('fault', ['late_future','rule','canonical_rule','field','future_offset','future_length','static_shape','future_dtype','static_dimension','overlap'])
def test_transformer_streams_reject(declarations, fault):
    value = clone(declarations['transformer'])
    if fault == 'late_future': value['known_future_available_at'] = ((day(3),),(day(3),))
    elif fault == 'rule': value['future_availability'][0]['rule'] = 'observed_only'
    elif fault == 'canonical_rule': value['future_availability'][0]['rule'] = 'external_forecast'
    elif fault == 'field': value['future_availability'][0]['field'] = 'lag1'
    elif fault == 'future_offset': value['future_offsets'] = (1,)
    elif fault == 'future_length': value['known_future_masks']['lengths'] = (0,1)
    elif fault == 'static_shape': value['static_shape'] = (2,2)
    elif fault == 'future_dtype': value['known_future']['features'][0]['dtype'] = 'float64';value['known_future']['features'][0]['numeric_policy']='strict_exact_float64_v1'
    elif fault == 'static_dimension': value['static']['features'] = (dict(name='promotion',dtype='float32',numeric_policy='strict_exact_float32_v1'),)
    else: value['known_future']['features'] = (dict(name='lag1',dtype='float32',numeric_policy='strict_exact_float32_v1'),)
    with pytest.raises(EvaluationDeclarationError): validate_representation(value)


def test_compatibility_excludes_capture_but_rejects_schema_or_state_change(declarations):
    a = validate_representation(declarations['recurrent'])
    value = clone(declarations['recurrent'])
    value['binding']['historical_revision'] = 'another-revision'
    b = validate_representation(value)
    assert a.representation_id != b.representation_id
    assert require_compatible(a,b) == a.compatibility_id
    value['binding']['scaler']['state_digest'] = '9'*64
    c = validate_representation(value)
    with pytest.raises(EvaluationDeclarationError): require_compatible(a,c)
    with pytest.raises(EvaluationDeclarationError): require_compatible(a, validate_representation(declarations['tabular']))


@pytest.mark.parametrize('entry', ['dict','json','constructor','nested'])
def test_secret_diagnostics_are_sanitized(declarations, capsys, caplog, entry):
    secret = 'M2_SECRET_' + os.urandom(8).hex()
    value = clone(declarations['tabular'])
    value[secret] = secret
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:
            if entry == 'dict': validate_representation(value)
            elif entry == 'json':
                wire = validate_representation(declarations['tabular']).model_dump(mode='json')
                wire[secret] = secret
                validate_representation(json.dumps(wire))
            elif entry == 'constructor': TabularRepresentation(**value)
            else: InputFeature(name='lag1',dtype='invalid',numeric_policy=secret)
        except EvaluationDeclarationError as error:
            diagnostic = str(error)+repr(error)+repr(error.args)+''.join(traceback.format_exception(error))
            assert error.__cause__ is None and error.__context__ is None
        else: pytest.fail('malformed declaration accepted')
    output = capsys.readouterr()
    assert secret not in diagnostic+output.out+output.err+caplog.text+repr([str(w.message) for w in seen])


def test_frameworks_and_execution_not_imported_in_fresh_process():
    script = '''
import importlib.abc,sys
blocked=('torch','tensorflow','xgboost','lightgbm','src.services','src.database','mlflow')
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if any(name==p or name.startswith(p+'.') for p in blocked):raise AssertionError(name)
sys.meta_path.insert(0,Guard())
import src.data.contracts.model_representation
assert not any(name==p or name.startswith(p+'.') for name in sys.modules for p in blocked)
'''
    result = subprocess.run([sys.executable,'-c',script],text=True,capture_output=True,timeout=30,
                            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='.'))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('method', ['model_validate','model_validate_json','model_validate_strings'])
def test_callers_cannot_relax_validation(method):
    value = dict(name='lag1',dtype='float32',numeric_policy='strict_exact_float32_v1')
    if method == 'model_validate_json': value = json.dumps(value)
    with pytest.raises(EvaluationDeclarationError):
        getattr(InputFeature, method)(value, strict=False)


@pytest.mark.parametrize('method', ['model_validate','model_validate_json','model_validate_strings'])
def test_nested_public_ingress_roundtrips_and_unknown_fields(method, capsys):
    value = dict(name='lag1',dtype='float32',numeric_policy='strict_exact_float32_v1')
    encoded = json.dumps(value) if method == 'model_validate_json' else value
    result = getattr(InputFeature, method)(encoded)
    assert result.name == 'lag1'
    secret = 'NESTED_' + os.urandom(8).hex()
    bad = dict(value, **{secret: secret})
    if method == 'model_validate_json': bad = json.dumps(bad)
    with pytest.raises(EvaluationDeclarationError) as exc:
        getattr(InputFeature, method)(bad)
    assert secret not in ''.join(traceback.format_exception(exc.value))
    assert exc.value.__context__ is None


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
def test_validation_never_fits_or_transforms(declarations, monkeypatch, family):
    def forbidden(*args, **kwargs): raise AssertionError('preprocessing executed')
    for name in ('fit_state','prepare_training','prepare_inference','_calculate'):
        monkeypatch.setattr(CanonicalFeaturePipeline, name, forbidden)
    assert validate_representation(declarations[family]).family == family


def test_right_padding_and_strided_calendar_slots(declarations):
    value = clone(declarations['recurrent'])
    value['window'].update(lookback=5, stride=2, past_offsets=(-5,-3,-1), padding='right')
    value['masks']['padding'] = ((False,False,True),(False,False,False))
    value['masks']['missing'] = (((False,),(False,),(True,)),((False,),(False,),(False,)))
    value['available_at'] = ((day(2),day(2),None),(day(3),day(3),day(3)))
    result = validate_representation(value)
    assert result.window.past_offsets == (-5,-3,-1)
    assert result.masks.lengths == (2,3)


def test_missing_masks_are_explicit_not_silent_imputation(declarations):
    value = clone(declarations['recurrent'])
    value['feature_schema']['features'][0]['missing'] = 'mask'
    value['masks']['missing'] = (((True,),(True,),(False,)),((False,),(False,),(False,)))
    value['available_at'] = ((None,None,day(2)),(day(3),day(3),day(3)))
    result = validate_representation(value)
    assert result.masks.missing[0][1] == (True,)


def test_optional_transformer_groups_are_explicit_zero_width(declarations):
    value = clone(declarations['transformer'])
    value.update(static=dict(features=()),static_shape=(2,0),static_missing_mask=((),()),static_available_at=(None,None),
                 known_future=dict(features=()),known_future_shape=(2,1,0),future_availability=(),
                 known_future_available_at=((None,),(None,)))
    value['known_future_masks']['missing'] = (((),),((),))
    assert validate_representation(value).known_future_shape == (2,1,0)


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
def test_multihorizon_geometry_is_declaration_only(declarations, family):
    from src.features.feature_schema_compatibility import FeatureSchemaCompatibility
    value = clone(declarations[family])
    plan = value['binding']['plan'].model_dump()
    plan.update(forecast_horizon=2,capability='planning_only')
    plan = TemporalPreparationPlan.model_validate(plan)
    schema = value['binding']['canonical_schema'].model_dump()
    schema['forecast_horizon'] = 2
    value['binding'].update(plan=plan,canonical_schema=FeatureSchemaCompatibility.model_validate(schema))
    if value['binding'].get('scaler') is not None:
        value['binding']['scaler']['training_plan_digest'] = plan.digest
        value['binding']['scaler']['source_schema_id'] = value['binding']['canonical_schema'].schema_id
    points=[]
    for original in value['samples']:
        first = dict(original[0],plan_digest=plan.digest)
        second = dict(first,horizon_step=2,target_period=first['target_period_end'],target_period_end=first['target_period_end']+timedelta(weeks=1))
        points.append((first,second))
    value['samples'] = tuple(points)
    if family == 'transformer':
        value.update(known_future_shape=(2,2,1),future_offsets=(0,1),
                     known_future_available_at=((day(2),day(2)),(day(3),day(3))))
        value['known_future_masks'] = dict(lengths=(2,2),padding=((False,False),(False,False)),missing=(((False,),(False,)),((False,),(False,))))
    result = validate_representation(value)
    assert len(result.samples[0]) == 2 and result.binding.plan.capability == 'planning_only'
    bad = result.model_dump()
    bad['samples'] = tuple((g[1],g[0]) for g in bad['samples'])
    with pytest.raises(EvaluationDeclarationError):validate_representation(bad)


def test_legacy_synthetic_c1_c2_bytes_and_digests_are_pinned():
    import hashlib
    import runpy
    from src.data.contracts.forecast_comparison import JointComparisonPolicy
    from src.data.contracts.forecast_evaluation import ScoringArithmeticPolicy
    from src.data.contracts.model_evaluation import ModelEvaluationEligibility
    predictor = runpy.run_path('tests/data/contracts/test_backtesting.py')['cap']()
    joint = runpy.run_path('tests/data/contracts/test_forecast_comparison.py')['joint_plan'].__wrapped__()
    policy = JointComparisonPolicy(policy_id='synthetic',arithmetic=ScoringArithmeticPolicy(policy_id='exact'))
    def h(text): return hashlib.sha256(text.encode()).hexdigest()
    assert (h(predictor.model_dump_json()),predictor.digest) == (
        '59c5d91731e70607b54b6c85ea83a368f055a686c747f128a0beefc078befa35',
        'ff9c8e5710e44c3f93175b355ad18f8811faf4b89d80d3dc741f795f8c3f24db')
    assert (h(joint.model_dump_json()),joint.digest) == (
        '8418cc1c5b70b5c38aa258a1c10c05437332e9cdb08669347b75e6bf5974c2d2',
        '895481b16f066e8874f27c97a1e752e9b83fe74d327ef2b7ce4f741a8bf330cf')
    assert (h(policy.model_dump_json()),policy.digest) == (
        '038cee755f686446e0968c8aeceb1d0b7318e2da1c0d13e86e7dbec8cc8a2775',
        'e00573f63592015a1b3587f277576c962d6306f282645693f41bb8cf90540bbd')
    assert h(operational_json(ModelEvaluationEligibility.model_json_schema())) == '4eb6357902ae7c6cff4421c35db7e0a474f5d2c02f785f7e5eb3cf24acc3accc'


@pytest.mark.parametrize('field,value', [('canonical_state_id','9'*64),('source_schema_id','9'*64),
                                       ('organization_ids',(UUID(int=99),)),('fold_id','another-fold')])
def test_scaler_scope_substitution_rejects(declarations, field, value):
    raw = clone(declarations['recurrent'])
    raw['binding']['scaler'][field] = value
    with pytest.raises(EvaluationDeclarationError):validate_representation(raw)


@pytest.mark.parametrize('fault', ['bool_as_number','integer_from_float','wrong_vocabulary','lost_vocabulary','dimensions'])
def test_feature_semantics_and_context_reject(declarations, fault):
    raw = clone(declarations['tabular'])
    if fault == 'bool_as_number':
        raw['feature_schema']['features'][1].update(dtype='bool',numeric_policy='strict_boolean_v1')
    elif fault == 'integer_from_float':
        raw['feature_schema']['features'][1].update(dtype='int32',numeric_policy='strict_integer_v1')
    elif fault == 'wrong_vocabulary':
        raw['feature_schema']['features'][0]['vocabulary_field'] = 'promotion'
    elif fault == 'lost_vocabulary':
        raw['feature_schema']['features'][0].update(categorical_policy='none',vocabulary_field=None)
    else:
        raw['samples'][0][0]['dimensions'] = (('unapproved','value'),)
    with pytest.raises(EvaluationDeclarationError):validate_representation(raw)


def test_json_schema_and_unrelated_warnings(declarations):
    for cls in (TabularRepresentation, RecurrentRepresentation, TransformerRepresentation):
        schema = cls.model_json_schema()
        assert schema['additionalProperties'] is False
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        validate_representation(declarations['tabular'])
        warnings.warn('unrelated representation test warning', UserWarning)
    assert [str(w.message) for w in seen] == ['unrelated representation test warning']


def test_revalidation_rejects_malformed_constructed_model_without_diagnostics(declarations, capsys):
    valid = validate_representation(declarations['tabular'])
    secret = 'CONSTRUCT_' + os.urandom(8).hex()
    malformed = valid.model_copy(update={'shape': (secret,2)})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:
            validate_representation(malformed)
    output = capsys.readouterr()
    assert secret not in repr(seen)+output.out+output.err+''.join(traceback.format_exception(exc.value))


def test_canonical_bytes_ignore_mapping_insertion_order(declarations):
    import hashlib
    original = validate_representation(declarations['tabular'])
    raw = original.model_dump()
    cfg = raw['binding']['canonical_schema']['configuration']
    for key in ('output_dtypes','missing_value_behavior','category_encoding'):
        cfg[key] = dict(reversed(list(cfg[key].items())))
    restored = validate_representation(raw)
    assert restored.canonical_json == original.canonical_json
    assert original.canonical_bytes == original.canonical_json.encode('utf-8')
    assert hashlib.sha256(original.canonical_bytes).hexdigest() == original.representation_id


def test_capture_evidence_changes_full_identity_not_schema_compatibility(declarations):
    original = validate_representation(declarations['transformer'])
    raw = clone(declarations['transformer'])
    raw['future_availability'][0]['evidence_identity'] = '9'*64
    other = validate_representation(raw)
    assert original.representation_id != other.representation_id
    assert require_compatible(original,other) == original.compatibility_id


def test_malformed_compatibility_identity_is_sanitized(declarations):
    secret = 'COMPAT_' + os.urandom(8).hex()
    valid = validate_representation(declarations['tabular'])
    malformed = valid.model_copy(update={'shape': (secret,2)})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc: malformed.compatibility_id
    assert not seen and secret not in ''.join(traceback.format_exception(exc.value))
    assert exc.value.__cause__ is None and exc.value.__context__ is None


@pytest.mark.parametrize('fault', ['configuration_id','measurement'])
def test_nested_malformed_foundation_models_cannot_emit_serializer_warnings(declarations, fault, capsys, caplog):
    from src.data.contracts.common import Measurement
    secret = 'NESTED_MODEL_' + os.urandom(8).hex()
    raw = clone(declarations['tabular'])
    schema = raw['binding']['canonical_schema']
    configuration = schema.configuration
    if fault == 'configuration_id':
        bad = configuration.model_copy(update={'configuration_id': {secret: secret}})
    else:
        measurement = Measurement.model_construct(metric='reported',unit={secret: secret},currency='USD')
        execution = configuration.execution.model_copy(update={'measurement': measurement})
        bad = configuration.model_copy(update={'execution': execution})
    raw['binding']['canonical_schema'] = schema.model_copy(update={'configuration': bad})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc: validate_representation(raw)
    out = capsys.readouterr()
    diagnostic = str(exc.value)+repr(exc.value)+repr(exc.value.args)+''.join(traceback.format_exception(exc.value))
    assert not seen and secret not in diagnostic+out.out+out.err+caplog.text
    assert exc.value.__context__ is None and exc.value.__cause__ is None


def duplicate_wire(valid, fault):
    wire = valid.canonical_json
    if fault in ('version','shape','axes'):
        value = {'version': '999.0', 'shape': [999,999], 'axes': ['features','batch']}[fault]
        return '{' + json.dumps(fault) + ':' + json.dumps(value) + ',' + wire[1:]
    if fault == 'numeric_policy':
        needle = '"numeric_policy":"strict_exact_float32_v1"'
        replacement = '"numeric_policy":"unsupported","numeric_policy":"strict_exact_float32_v1"'
    elif fault == 'nested_object':
        needle = '"historical_revision":"immutable-history-v1"'
        replacement = '"historical_revision":"wrong","historical_revision":"immutable-history-v1"'
    elif fault == 'nested_array':
        needle = '"horizon_step":1'
        replacement = '"horizon_step":999,"horizon_step":1'
    elif fault == 'deep_parameters':
        needle = '"offset":1'
        replacement = '"offset":999,"offset":1'
    else:
        # Keys collide after JSON escape decoding, not merely raw-byte equality.
        needle = '"numeric_policy":"strict_exact_float32_v1"'
        replacement = '"numeric_policy":"unsupported","numeric_\\u0070olicy":"strict_exact_float32_v1"'
    assert needle in wire
    return wire.replace(needle,replacement,1)


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
@pytest.mark.parametrize('fault', ['version','shape','axes','numeric_policy','nested_object','nested_array','deep_parameters','escaped_key'])
@pytest.mark.parametrize('entry', ['factory','direct'])
def test_duplicate_keys_reject_recursively_before_pydantic(declarations, monkeypatch, family, fault, entry):
    from src.data.contracts.model_evaluation import _Declaration
    from unittest.mock import Mock
    valid = validate_representation(declarations[family])
    wire = duplicate_wire(valid,fault)
    # A standard last-wins parser would produce the original accepted content.
    assert json.loads(wire) == json.loads(valid.canonical_json)
    called = Mock(side_effect=AssertionError('Pydantic must not receive duplicate-key JSON'))
    monkeypatch.setattr(_Declaration, 'model_validate_json', classmethod(lambda cls,*a,**kw: called(*a,**kw)))
    operation = validate_representation if entry == 'factory' else type(valid).model_validate_json
    with pytest.raises(EvaluationDeclarationError): operation(wire)
    called.assert_not_called()


@pytest.mark.parametrize('contract', ['feature','features','window','masks','scaler','binding'])
@pytest.mark.parametrize('encoding', ['str','bytes','bytearray'])
def test_nested_m2_direct_json_ingress_rejects_duplicates(declarations, contract, encoding):
    root = validate_representation(declarations['recurrent'])
    nested = {'feature':root.feature_schema.features[0], 'features':root.feature_schema,
              'window':root.window, 'masks':root.masks, 'scaler':root.binding.scaler,
              'binding':root.binding}[contract]
    wire = nested.canonical_json
    key, value = next(iter(json.loads(wire).items()))
    bad = '{' + json.dumps(key) + ':' + json.dumps(value) + ',' + wire[1:]
    def encode(text):
        return text if encoding == 'str' else text.encode() if encoding == 'bytes' else bytearray(text.encode())
    restored = type(nested).model_validate_json(json_data=encode(wire))
    assert restored.canonical_bytes == nested.canonical_bytes
    with pytest.raises(EvaluationDeclarationError): type(nested).model_validate_json(json_data=encode(bad))


@pytest.mark.parametrize('family', ['tabular','recurrent','transformer'])
@pytest.mark.parametrize('encoding', ['str','bytes','bytearray'])
def test_strict_json_preserves_valid_identity_and_compatibility(declarations, family, encoding):
    original = validate_representation(declarations[family])
    wire = original.canonical_json
    encoded = wire if encoding == 'str' else wire.encode() if encoding == 'bytes' else bytearray(wire.encode())
    factory = validate_representation(encoded)
    direct = type(original).model_validate_json(json_data=encoded)
    assert original.canonical_bytes == factory.canonical_bytes == direct.canonical_bytes
    assert original.representation_id == factory.representation_id == direct.representation_id
    assert require_compatible(original,factory) == require_compatible(original,direct) == original.compatibility_id


@pytest.mark.parametrize('entry', ['factory','direct','nested'])
@pytest.mark.parametrize('fault', ['duplicate','syntax','utf8','utf16','surrogate','escaped_surrogate','constant'])
def test_invalid_json_encoding_and_duplicates_have_sanitized_deterministic_errors(
    declarations, capsys, caplog, entry, fault,
):
    valid = validate_representation(declarations['tabular'])
    secret = 'JSON_SECRET_' + os.urandom(8).hex()
    if fault == 'duplicate': payload = '{' + json.dumps(secret) + ':1,' + json.dumps(secret) + ':2}'
    elif fault == 'syntax': payload = '{' + json.dumps(secret) + ':'
    elif fault == 'utf8': payload = ('{"name":"'+secret+'"}').encode()+b'\xff'
    elif fault == 'utf16': payload = ('{"name":"'+secret+'"}').encode('utf-16')
    elif fault == 'surrogate': payload = '{"'+secret+'":"' + chr(0xD800) + '"}'
    elif fault == 'escaped_surrogate': payload = '{"'+secret+'":"' + r'\ud800' + '"}'
    else: payload = '{"'+secret+'":NaN}'
    operation = (validate_representation if entry == 'factory' else
                 type(valid).model_validate_json if entry == 'direct' else InputFeature.model_validate_json)
    messages = []
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        for _ in range(2):
            # Verify detachment even while a caller is handling a sensitive error.
            try:
                raise RuntimeError(secret)
            except RuntimeError:
                with pytest.raises(EvaluationDeclarationError) as caught: operation(payload)
                error = caught.value
                assert error.__context__ is None and error.__cause__ is None
                diagnostic = str(error)+repr(error)+repr(error.args)+''.join(traceback.format_exception(error))
                assert secret not in diagnostic
                messages.append(str(error))
    out = capsys.readouterr()
    assert not seen and secret not in out.out+out.err+caplog.text
    assert messages == ['invalid non-authoritative evaluation declaration']*2
