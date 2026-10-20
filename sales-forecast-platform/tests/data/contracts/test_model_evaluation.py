"""D3-A metadata only. Inert artifact bytes are NEVER a real/issued model.

The accepted D1 service supplies actual authorized preparation metadata; no
XGBoost import, training, loading or prediction is permitted in this suite.
"""
import builtins
from dataclasses import is_dataclass, replace
from datetime import timedelta
import hashlib
import json
import logging
import runpy
import sys
import traceback
from uuid import UUID
import warnings

import numpy as np
import pytest

from src.data.contracts.backtesting import PredictorCapabilities, ForecastEvaluationPlan
from src.data.contracts.dataset import operational_json
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.model_candidate import (CandidateBundle,CandidateManifest,OfflineXGBoostConfiguration,
    RuntimeIdentity,TrainingInvocation,assemble_candidate,digest,strict_float32)
from src.data.contracts.model_evaluation import (FittedCandidateCapabilities,PublicationRelationshipDeclaration,
    ValidationOnlyCoverage,ValidationH1Period,SeriesEvaluationScope,EvaluationResourcePolicy,
    EvaluationAuthorizationRequirements,ModelEvaluationEligibility,EvaluationDeclarationError,declare_evaluation_eligibility)
from src.data.contracts.temporal_preparation import TemporalPreparationPlan,TemporalDatasetBinding
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility


@pytest.fixture(autouse=True, scope='module')
def forbid_native_model_operations():
    original=builtins.__import__
    def guarded(name,*a,**kw):
        if name=='xgboost' or name.startswith('xgboost.'):
            raise AssertionError('D3-A must not import or operate XGBoost')
        return original(name,*a,**kw)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(builtins,'__import__',guarded)
        yield


@pytest.fixture(scope='module')
def metadata(tmp_path_factory):
    make=runpy.run_path('tests/training/test_canonical_model_training_preparation.py')['make_training'].__wrapped__(
        tmp_path_factory.mktemp('d3a-declaration-fixture'))
    b=make();cfg=b['configuration'].model_dump()
    cfg.update(configuration_id='d3a-declaration-fixture',version='2.3',ordered_outputs=('location','lag1','mean4'))
    cfg['calculations']=tuple(c for c in cfg['calculations'] if c['output']!='std4')
    cfg['missing_value_behavior'].pop('std4');cfg['output_dtypes'].pop('std4')
    b['configuration']=FeatureConfiguration.model_validate(cfg);data=b['plan'].model_dump()
    data.update(configuration_id=b['configuration'].configuration_id,configuration_version=b['configuration'].version,
                configuration_digest=CanonicalFeaturePipeline(b['configuration']).digest)
    b['plan']=TemporalPreparationPlan.model_validate(data);b['bootstrap']()
    handoff=b['service'].prepare_training(b['plan'],b['configuration']);provenance=handoff.provenance
    options=OfflineXGBoostConfiguration();x=strict_float32(handoff.matrix.to_numpy());y=strict_float32(handoff.labels.to_numpy())
    # Honest inert fixture: content-valid candidate-shaped metadata cannot prove
    # that any Booster was fitted or that a D2 owner issued these bytes.
    artifact=b'INERT D3-A CONTRACT FIXTURE; NOT A NATIVE MODEL'
    runtime=RuntimeIdentity(python='3.12.14',xgboost='3.2.0',numpy='2.3.5',pandas='2.3.3',platform='declaration-only',
        source_commit='d7a81bdf124028852ebe81ac927bd52069c8018a',source_files=(('fixture.py','7'*64),),source_matches_commit=True)
    manifest=CandidateManifest(invocation=TrainingInvocation(invocation_id='declared-training-invocation',
        fixture_approval_id='inert-metadata-only',release_authority='release',historical_value_authority='values',
        category_authority='categories',preparation_authority='preparation',training_authority='training'),
        training=options,runtime=runtime,scope=b['scope'],provenance_json=provenance.model_dump_json(),
        parameters_json=operational_json(options.parameters),feature_names=tuple(handoff.matrix.columns),
        feature_dtypes=tuple(str(d) for d in handoff.matrix.dtypes),row_index=tuple(
            tuple(v.isoformat() if hasattr(v,'isoformat') else str(v) for v in row) for row in handoff.matrix.index),
        matrix_float32_hex=x.astype('<f4').tobytes().hex(),labels_float32_hex=y.astype('<f4').tobytes().hex(),
        predictions_float32_hex=np.zeros(len(x),dtype='<f4').tobytes().hex(),
        state_digest=digest(provenance.states[0].state_json.encode()),artifact_digest=digest(artifact),artifact_size=len(artifact))
    bundle=assemble_candidate(manifest,artifact);native=provenance.states[0];schema=FeatureSchemaCompatibility.model_validate_json(native.representation_json)
    capabilities=FittedCandidateCapabilities(candidate_bundle_id=bundle.bundle_id,artifact_digest=manifest.artifact_digest,
        training_handoff_id=provenance.handoff_id,configuration_digest=provenance.configuration_digest,
        fitted_state_id=native.state.state_id,fitted_state_digest=manifest.state_digest,representation_schema_id=schema.schema_id,
        feature_names=manifest.feature_names,feature_dtypes=manifest.feature_dtypes)
    training=provenance.plan.datasets[0];reference=replace(training.reference,attempt_id=UUID(int=999),version_id='8'*64)
    evaluation=TemporalDatasetBinding.model_validate({**training.model_dump(),'reference':reference,'manifest_digest':'8'*64,'admission_digest':'9'*64})
    dimensions={claim.series_id:claim.dimensions for claim in provenance.labels}
    relationship=PublicationRelationshipDeclaration(relationship_id='operator-must-verify',authority_id='relationship-authority',authority_version='1.0',
        evidence_references=('independent-ledger-reference',),training=training,evaluation=evaluation,
        metric=b['configuration'].target_metric,unit=b['configuration'].execution.measurement.unit,currency=b['configuration'].execution.measurement.currency,frequency='weekly',calendar='gregorian',timezone='UTC',
        series=tuple(SeriesEvaluationScope(series_id=s,dimensions=dimensions[s]) for s in training.series_ids))
    fold=provenance.plan.folds[0]
    coverage=ValidationOnlyCoverage(fold_id=fold.fold_id,periods=tuple(ValidationH1Period(
        forecast_origin=o,target_period=o,target_period_end=o+timedelta(days=7)) for o in fold.validation_origins))
    return dict(bundle=bundle,capabilities=capabilities,relationship=relationship,coverage=coverage,runtime=runtime)


def declare(b,**changes):
    return declare_evaluation_eligibility(b['bundle'],**{**{k:v for k,v in b.items() if k!='bundle'},**changes})


def changed(obj,**changes):
    if is_dataclass(obj):return replace(obj,**changes)
    return type(obj).model_construct(**{**obj.__dict__,**changes})


def test_declaration_is_not_native_issuance_or_approval(metadata):
    result=declare(metadata)
    assert result.status=='DECLARATION_ONLY' and result.authorization.issuance=='NOT_IMPLEMENTED'
    assert result.authorization.issuer=='original_live_d2_issuer'
    assert not hasattr(result,'authorize') and not hasattr(result,'predict')
    clone=CandidateBundle(bytes(bytearray(metadata['bundle'].payload)))
    assert clone is not metadata['bundle']
    clone_data={**metadata,'bundle':clone}
    assert declare(clone_data)==result  # Content equality never proves live ownership.
    assert result.manifest.provenance.plan.model_dump_json()==metadata['bundle'].manifest.provenance.plan.model_dump_json()
    assert result.manifest.provenance.states[0].state_json==metadata['bundle'].manifest.provenance.states[0].state_json
    assert ModelEvaluationEligibility.model_validate_json(result.model_dump_json())==result
    assert result.eligibility_id==declare(metadata).eligibility_id


@pytest.mark.parametrize('field,value',[
 ('implementation_kind','synthetic'),('model_family','lightgbm'),('supported_horizon',2),('supported_horizon',True),
 ('prediction_coverage','training'),('prediction_coverage','test'),('strategy','direct_multi_horizon'),
 ('representation_kind','temporal_sequence'),('model_input_policy','approximate_float32'),
 ('widening_policy','rounded_float64'),('native_output_policy','float64'),('ownership_requirement','archival_import'),('version','1.0')])
def test_unsupported_capability_declarations_reject(metadata,field,value):
    with pytest.raises(ValueError):FittedCandidateCapabilities.model_validate({**metadata['capabilities'].model_dump(),field:value})


@pytest.mark.parametrize('field,value',[
 ('candidate_bundle_id','1'*64),('artifact_digest','1'*64),('training_handoff_id','1'*64),('configuration_digest','1'*64),
 ('fitted_state_id','1'*64),('fitted_state_digest','1'*64),('representation_schema_id','1'*64),
 ('feature_names',('mean4','lag1','location')),('feature_dtypes',('int8','float32','float64')),('allowed_covariates',('undeclared',))])
def test_original_candidate_and_state_bindings_are_required(metadata,field,value):
    with pytest.raises(EvaluationDeclarationError):declare(metadata,capabilities=changed(metadata['capabilities'],**{field:value}))


@pytest.mark.parametrize('field,value',[
 ('source_id',UUID(int=700)),('mapping_id','foreign'),('mapping_version','9.0.0'),('mapping_digest','1'*64),
 ('series_definition_digest','1'*64),('series_ids',(UUID(int=900),)),('quality_policy_id','other')])
def test_incompatible_publication_relationship_rejects(metadata,field,value):
    rel=metadata['relationship'];foreign=changed(rel.evaluation,**{field:value})
    with pytest.raises(EvaluationDeclarationError):declare(metadata,relationship=changed(rel,evaluation=foreign))


@pytest.mark.parametrize('field,value',[
 ('metric','other'),('unit','units'),('currency','EUR'),('frequency','daily'),('calendar','iso_week'),
 ('timezone','Europe/Berlin'),('period_anchor','end'),('training_purpose','offline_forecast_evaluation'),
 ('evaluation_purpose','production_training')])
def test_measurement_calendar_and_purpose_mixing_rejects(metadata,field,value):
    with pytest.raises(EvaluationDeclarationError):declare(metadata,relationship=changed(metadata['relationship'],**{field:value}))


def test_cross_tenant_and_same_publication_reject(metadata):
    rel=metadata['relationship'];reference=changed(rel.evaluation.reference,organization_id=UUID(int=999))
    with pytest.raises(EvaluationDeclarationError):declare(metadata,relationship=changed(rel,evaluation=changed(rel.evaluation,reference=reference)))
    with pytest.raises(EvaluationDeclarationError):declare(metadata,relationship=changed(rel,evaluation=rel.training))
    wrong=changed(rel.series[0],dimensions=(('location','forged'),))
    with pytest.raises(EvaluationDeclarationError):declare(metadata,relationship=changed(rel,series=(wrong,)+rel.series[1:]))


@pytest.mark.parametrize('partition',('training','test','unlisted'))
def test_only_approved_validation_origins_execute(metadata,partition):
    plan=metadata['bundle'].manifest.provenance.plan;fold=plan.folds[0];coverage=metadata['coverage']
    origin=(fold.training_origins[0] if partition=='training' else fold.test_origins[0] if partition=='test' else fold.validation_origins[0]+timedelta(days=1))
    period=ValidationH1Period(forecast_origin=origin,target_period=origin,target_period_end=origin+timedelta(days=7))
    with pytest.raises(EvaluationDeclarationError):declare(metadata,coverage=changed(coverage,periods=(period,)))


@pytest.mark.parametrize('case',('wrong_start','wrong_end','h2','duplicate','reordered','foreign_fold'))
def test_h1_coverage_rejects_ambiguous_periods(metadata,case):
    c=metadata['coverage'];p=c.periods[0]
    changes={'wrong_start':dict(periods=(changed(p,target_period=p.target_period+timedelta(days=7)),)),
             'wrong_end':dict(periods=(changed(p,target_period_end=p.target_period_end+timedelta(days=7)),)),
             'h2':dict(periods=(changed(p,horizon_step=2),)), 'duplicate':dict(periods=(p,p)),
             'reordered':dict(periods=(changed(p,forecast_origin=p.forecast_origin+timedelta(days=7)),p)),
             'foreign_fold':dict(fold_id='other-fold')}[case]
    with pytest.raises(EvaluationDeclarationError):declare(metadata,coverage=changed(c,**changes))


@pytest.mark.parametrize('field,value',[
 ('max_origins',0),('max_prediction_rows',513),('max_native_calls',True),('max_elapsed_seconds',61),('max_input_cells',8193)])
def test_resource_policy_ceiling_types_and_bounds(field,value):
    with pytest.raises(ValueError):EvaluationResourcePolicy(**{field:value})


@pytest.mark.parametrize('field',('max_prediction_rows','max_input_cells','max_artifact_bytes','max_bundle_bytes','max_state_bytes'))
def test_aggregate_resource_declaration_refusal(metadata,field):
    with pytest.raises(EvaluationDeclarationError):declare(metadata,resources=EvaluationResourcePolicy(**{field:1}))


@pytest.mark.parametrize('field,value',[
 ('source_commit','1'*40),('platform','other'),('xgboost','3.1.0'),('source_matches_commit',False),('source_files',(('other.py','2'*64),))])
def test_runtime_source_pinning(metadata,field,value):
    with pytest.raises(EvaluationDeclarationError):declare(metadata,runtime=changed(metadata['runtime'],**{field:value}))


@pytest.mark.parametrize('field,value',[
 ('issuance','AUTHORIZED'),('issuer','caller'),('candidate_reconstruction','allowed'),
 ('purpose','production_training'),('required_stages',('admit',)),('approved',True),
 ('cancellation','optional'),('deadline','elapsed_gt_budget'),('terminal_ordering','scopes_before_global'),
 ('training_handoff','reconstructed')])
def test_no_public_approval_or_revocation_bypass(field,value):
    with pytest.raises(ValueError):EvaluationAuthorizationRequirements(**{field:value})


def test_synthetic_v1_identity_is_frozen_and_no_fitted_fallback(metadata):
    # Literal parent serialization, not two calls to the same new implementation.
    expected=(b'{"version":"1.0","predictor_id":"synthetic","predictor_version":"1","implementation_kind":"synthetic",'
       b'"strategy":"recursive_one_step","representation_kind":"tabular_step","representation_schema_id":"'+b'1'*64+
       b'","numeric_policy_id":"'+b'2'*64+b'","lookback":4,"supported_horizon":3,"allowed_covariates":[],"max_absolute_prediction":1.7976931348623157e+308,'
       b'"output_dtype":"float64","output_semantics":"point_value","deterministic":true}')
    synthetic=PredictorCapabilities(predictor_id='synthetic',predictor_version='1',strategy='recursive_one_step',representation_kind='tabular_step',
        representation_schema_id='1'*64,numeric_policy_id='2'*64,lookback=4,supported_horizon=3)
    assert synthetic.model_dump_json().encode()==expected
    assert synthetic.digest==hashlib.sha256(operational_json(json.loads(expected)).encode()).hexdigest()
    with pytest.raises(ValueError):PredictorCapabilities.model_validate(metadata['capabilities'].model_dump())
    assert metadata['capabilities'].implementation_kind!='synthetic'


def test_immutable_detached_and_ordered_representation(metadata):
    result=declare(metadata);cap=result.capabilities
    for obj,field,value in ((result,'status','AUTHORIZED'),(cap,'feature_names',('other',)),(result.runtime,'source_commit','0'*40)):
        with pytest.raises(ValueError):setattr(obj,field,value)
    data=result.model_dump();data['capabilities']['feature_names']=('other',)
    assert result.capabilities.feature_names==metadata['bundle'].manifest.feature_names
    assert result.manifest is not result.manifest
    assert cap.numeric_policy_id==declare(metadata).capabilities.numeric_policy_id


@pytest.mark.parametrize('field',('capabilities','relationship','coverage','runtime'))
@pytest.mark.parametrize('channel',('warnings','stderr','logging'))
def test_secret_bearing_declarations_are_sanitized(metadata,capsys,caplog,field,channel):
    secret='D3ASEC91';obj=metadata[field]
    mutation={'capabilities':dict(feature_names=(secret,)), 'relationship':dict(unit={secret:secret}),
              'coverage':dict(periods=(secret,)), 'runtime':dict(source_commit={secret:secret})}[field]
    bad=changed(obj,**mutation);capture_enabled=logging._warnings_showwarning is not None
    try:
        if channel=='logging':logging.captureWarnings(True)
        with warnings.catch_warnings(record=channel=='warnings') as caught:
            warnings.simplefilter('always')
            if channel=='stderr':
                def stderr_warning(message,category,filename,lineno,file=None,line=None):
                    print(warnings.formatwarning(message,category,filename,lineno,line),file=file or sys.stderr,end='')
                warnings.showwarning=stderr_warning
            with pytest.raises(EvaluationDeclarationError) as exc:declare(metadata,**{field:bad})
            text='' if caught is None else '\n'.join(str(w.message) for w in caught)
    finally:logging.captureWarnings(capture_enabled)
    out=capsys.readouterr()
    assert not text and not out.err
    assert secret not in text+out.out+out.err+caplog.text+''.join(traceback.format_exception(exc.value))
    with warnings.catch_warnings(record=True) as unrelated:
        warnings.simplefilter('always');warnings.warn('unrelated D3-A warning',UserWarning)
    assert len(unrelated)==1


@pytest.mark.parametrize('case',('m_after_o','same_date_partition','duplicate_training','reordered_training','horizon_two'))
def test_original_temporal_partitions_cannot_be_rewritten(metadata,case):
    body=json.loads(metadata['bundle'].payload)
    provenance=json.loads(body['manifest']['provenance_json'])
    plan=provenance['plan'];fold=plan['folds'][0]
    if case=='m_after_o':fold['model_fit_cutoff']=fold['assessment_cutoff']
    elif case=='same_date_partition':fold['validation_origins']=[fold['training_origins'][-1]]
    elif case=='duplicate_training':fold['training_origins']*=2
    elif case=='reordered_training':fold['training_origins'].reverse()
    else:plan['forecast_horizon']=2
    body['manifest']['provenance_json']=operational_json(provenance)
    bundle=CandidateBundle(operational_json(body).encode())
    with pytest.raises(EvaluationDeclarationError):declare({**metadata,'bundle':bundle})


@pytest.mark.parametrize('case',('foreign_fold','foreign_organization','state_bytes','configuration','training_plan'))
def test_original_state_and_partition_provenance_cannot_be_substituted(metadata,case):
    body=json.loads(metadata['bundle'].payload);p=json.loads(body['manifest']['provenance_json'])
    if case=='foreign_fold':p['states'][0]['fold_id']='foreign-fold'
    elif case=='foreign_organization':p['states'][0]['dataset']['reference']['organization_id']=str(UUID(int=700))
    elif case=='state_bytes':
        state=json.loads(p['states'][0]['state_json']);state['training_partition_reference']='1'*64
        p['states'][0]['state_json']=operational_json(state)
    elif case=='configuration':p['configuration_digest']='1'*64
    else:p['plan']['plan_id']='substituted-plan'
    body['manifest']['provenance_json']=operational_json(p)
    with pytest.raises(EvaluationDeclarationError):declare({**metadata,'bundle':CandidateBundle(operational_json(body).encode())})


def test_fitted_capability_does_not_enable_existing_synthetic_plan(metadata):
    p=metadata['bundle'].manifest.provenance.plan.model_dump()
    p.update(purpose='offline_forecast_evaluation',capability='recursive_rollout',predictor=metadata['capabilities'].model_dump())
    with pytest.raises(ValueError):ForecastEvaluationPlan.model_validate(p)


def test_digest_helper_only_receives_validated_configuration(metadata,monkeypatch):
    import src.features.canonical_feature_pipeline as pipeline
    original=pipeline.configuration_digest;calls=[]
    def validated(configuration):
        snapshot=FeatureConfiguration.model_validate(configuration.model_dump(warnings='error'))
        assert snapshot==configuration
        calls.append(configuration)
        return original(configuration)
    monkeypatch.setattr(pipeline,'configuration_digest',validated)
    declare(metadata)
    assert calls
    calls.clear()
    body=json.loads(metadata['bundle'].payload);p=json.loads(body['manifest']['provenance_json'])
    schema=json.loads(p['states'][0]['representation_json'])
    schema['configuration']['configuration_id']={'D3ASEC_DIGEST':'secret'}
    p['states'][0]['representation_json']=operational_json(schema)
    body['manifest']['provenance_json']=operational_json(p)
    with pytest.raises(EvaluationDeclarationError):declare({**metadata,'bundle':CandidateBundle(operational_json(body).encode())})
    assert not calls


@pytest.mark.parametrize('case',('manifest','configuration','labels'))
def test_malformed_embedded_candidate_snapshots_have_fixed_diagnostics(metadata,capsys,caplog,case):
    secret='D3ASEC_EMBEDDED';body=json.loads(metadata['bundle'].payload)
    if case=='manifest':body['manifest']['artifact_size']={secret:secret}
    else:
        p=json.loads(body['manifest']['provenance_json'])
        if case=='labels':p['labels']=[secret]
        else:
            schema=json.loads(p['states'][0]['representation_json'])
            schema['configuration']['execution']['measurement']['unit']={secret:secret}
            p['states'][0]['representation_json']=operational_json(schema)
        body['manifest']['provenance_json']=operational_json(p)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:
            declare({**metadata,'bundle':CandidateBundle(operational_json(body).encode())})
        data=declare(metadata).model_dump()
        data['candidate_manifest_json']=operational_json(body['manifest'])
        with pytest.raises(ValueError) as direct:ModelEvaluationEligibility.model_validate(data)
    out=capsys.readouterr()
    assert not caught and not out.err
    assert secret not in out.out+out.err+caplog.text+''.join(traceback.format_exception(exc.value))+''.join(traceback.format_exception(direct.value))


def test_declaration_never_refits_the_original_state(metadata,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('D3-A must never refit state')
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    result=declare(metadata)
    assert result.manifest.provenance.states[0].state_json==metadata['bundle'].manifest.provenance.states[0].state_json
    fold=result.manifest.provenance.plan.folds[0]
    assert fold.categorical_cutoff<=fold.model_fit_cutoff==result.coverage.periods[0].forecast_origin
