"""D3-A metadata only. Inert artifact bytes are NEVER a real/issued model.

The accepted D1 service supplies authorized metadata. One explicit bounded
native D2 regression uses genuine fitting/loading; other cases use inert bytes.
"""
import builtins
from dataclasses import is_dataclass, replace, fields
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
import pandas as pd
from collections.abc import Mapping
from pydantic import BaseModel, model_validator
import pytest

from src.data.contracts.backtesting import PredictorCapabilities, ForecastEvaluationPlan
from src.data.contracts.dataset import operational_json
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.model_candidate import (CandidateBundle,CandidateManifest,OfflineXGBoostConfiguration,
    RuntimeIdentity,TrainingInvocation,assemble_candidate,digest,strict_float32)
from src.data.contracts.model_evaluation import (FittedCandidateCapabilities,PublicationRelationshipDeclaration,
    ValidationOnlyCoverage,ValidationH1Period,SeriesEvaluationScope,EvaluationResourcePolicy,
    EvaluationAuthorizationRequirements,ModelEvaluationEligibility,LabelFreeCandidateProvenance,EvaluationDeclarationError,declare_evaluation_eligibility)
from src.data.contracts.temporal_preparation import TemporalPreparationPlan,TemporalDatasetBinding
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility


@pytest.fixture(autouse=True)
def forbid_native_model_operations(request):
    if request.node.name=='test_genuine_candidates_are_label_free_and_remain_owned':
        yield
        return
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
    assert result.candidate.training_plan.model_dump_json()==metadata['bundle'].manifest.provenance.plan.model_dump_json()
    assert result.candidate.fitted_state_digest==metadata['bundle'].manifest.state_digest
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
    assert not hasattr(result,'manifest') and not hasattr(result,'candidate_manifest_json')
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
        data['candidate_manifest_json']=operational_json(body['manifest'])  # Rejected legacy field must not be accepted.
        with pytest.raises(ValueError) as direct:ModelEvaluationEligibility.model_validate(data)
    out=capsys.readouterr()
    assert not caught and not out.err
    assert secret not in out.out+out.err+caplog.text+''.join(traceback.format_exception(exc.value))+''.join(traceback.format_exception(direct.value))


def test_declaration_never_refits_the_original_state(metadata,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('D3-A must never refit state')
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    result=declare(metadata)
    assert result.candidate.fitted_state_digest==metadata['bundle'].manifest.state_digest
    fold=result.candidate.training_plan.folds[0]
    assert fold.categorical_cutoff<=fold.model_fit_cutoff==result.coverage.periods[0].forecast_origin


_FORBIDDEN_PAYLOAD_FIELDS=frozenset({
    'manifest','candidate_manifest_json','provenance_json','labels','original_labels',
    'original_value_identity_json','matrix','row_index','matrix_float32_hex','labels_float32_hex',
    'predictions_float32_hex','artifact_base64','payload','state_json','vocabularies'})


def assert_label_free_graph(root):
    """Inspect actual stored objects, metadata accessors and structured dumps.

    This is a structural payload audit, not a search for coincidental numbers in
    legitimate calendar/budget/configuration metadata. Python internals aren't a
    sandbox; only supported repository properties are traversed.
    """
    import inspect
    from src.data.contracts.features import FeatureState
    from src.data.contracts.model_training import TrainingLabelClaim,TrainingFittedState,TrainingHandoffProvenance
    forbidden_types=(CandidateBundle,CandidateManifest,TrainingLabelClaim,TrainingFittedState,
                     TrainingHandoffProvenance,FeatureState,np.ndarray,pd.DataFrame,pd.Series,bytes,bytearray,memoryview)
    seen=set();visited=[];held=[]
    def visit(obj,path):
        assert not isinstance(obj,forbidden_types),(path,type(obj))
        if id(obj) in seen:return
        seen.add(id(obj));visited.append(path);held.append(obj)
        if isinstance(obj,BaseModel):
            names=set(type(obj).model_fields)
            assert not names&_FORBIDDEN_PAYLOAD_FIELDS,(path,names&_FORBIDDEN_PAYLOAD_FIELDS)
            assert set(vars(obj))==names and not obj.__pydantic_extra__ and not obj.__pydantic_private__,path
            for key in names:visit(getattr(obj,key),path+'.'+key)
            for name,member in inspect.getmembers(type(obj),lambda v:isinstance(v,property)):
                if not name.startswith('_') and member.fget.__module__.startswith('src.'):
                    assert name not in _FORBIDDEN_PAYLOAD_FIELDS,(path,name)
                    visit(getattr(obj,name),path+'.'+name)
        elif isinstance(obj,Mapping):
            assert not set(obj)&_FORBIDDEN_PAYLOAD_FIELDS,(path,set(obj)&_FORBIDDEN_PAYLOAD_FIELDS)
            for key,value in obj.items():visit(value,path+'.'+str(key))
        elif is_dataclass(obj):
            for field in fields(obj):visit(getattr(obj,field.name),path+'.'+field.name)
        elif isinstance(obj,(tuple,list,set,frozenset)):
            for i,value in enumerate(obj):visit(value,path+f'[{i}]')
        elif isinstance(obj,str) and obj[:1] in ('{','['):
            # Also detect a payload hidden inside an otherwise opaque JSON string.
            try:value=json.loads(obj)
            except (ValueError,TypeError):return
            visit(value,path+'.decoded')
    visit(root,'object')
    visit(root.model_dump(),'dump')
    visit(json.loads(root.model_dump_json()),'json')
    assert not hasattr(root,'manifest') and not hasattr(root,'candidate_manifest_json')
    assert not hasattr(root.candidate,'manifest')
    assert 'CandidateManifest' not in root.model_json_schema()['$defs']
    return visited


def assert_original_summary(result,bundle):
    manifest=bundle.manifest;p=manifest.provenance;s=p.states[0];c=result.candidate
    assert c.manifest_digest==digest(manifest.model_dump_json(warnings='error').encode())
    assert c.candidate_bundle_id==bundle.bundle_id and c.artifact_digest==manifest.artifact_digest
    assert c.training_invocation.model_dump_json()==manifest.invocation.model_dump_json()
    assert c.training_invocation_digest==digest(manifest.invocation.model_dump_json().encode())
    assert c.training_scope_digest==manifest.scope.digest and c.training_handoff_id==p.handoff_id
    assert c.preparation_id==p.preparation_id and c.training_plan_digest==p.plan.digest
    assert c.training_plan.model_dump_json()==p.plan.model_dump_json()
    assert c.fitted_state_digest==digest(s.state_json.encode()) and c.fitted_state_id==s.state.state_id
    assert c.fitted_state_size==len(s.state_json.encode())
    assert c.training_partition_reference==s.state.training_partition_reference
    assert c.representation.model_dump_json()==s.representation_json
    assert c.runtime_digest==digest(manifest.runtime.model_dump_json().encode())
    text=result.model_dump_json()+repr(result)
    for payload in (manifest.provenance_json,manifest.matrix_float32_hex,manifest.labels_float32_hex,
                    manifest.predictions_float32_hex,s.state_json):
        assert payload not in text
    for label in p.labels:assert label.original_value_identity_json not in text


def test_label_free_summary_object_graph_and_original_integrity(metadata):
    result=declare(metadata)
    assert len(assert_label_free_graph(result))>100
    assert_original_summary(result,metadata['bundle'])
    assert result.version=='1.1'
    restored=ModelEvaluationEligibility.model_validate_json(result.model_dump_json())
    assert restored==result and restored.eligibility_id==result.eligibility_id
    assert_label_free_graph(restored)


@pytest.mark.parametrize('field',('manifest','candidate_manifest_json','provenance_json','labels_float32_hex','row_index','predictions_float32_hex'))
def test_payload_injection_into_summary_is_forbidden(metadata,field):
    data=declare(metadata).model_dump();data['candidate'][field]='SENSITIVE_LABEL_PAYLOAD'
    with pytest.raises(ValueError) as exc:ModelEvaluationEligibility.model_validate(data)
    assert 'SENSITIVE_LABEL_PAYLOAD' not in str(exc.value)


@pytest.mark.parametrize('field,value',[
 ('fitted_state_id','1'*64),('configuration_digest','1'*64),('training_plan_digest','1'*64),
 ('training_invocation_digest','1'*64),('training_schema_digest','1'*64),('training_partition_reference','1'*64)])
def test_label_free_summary_bindings_cannot_be_replaced(metadata,field,value):
    data=declare(metadata).model_dump();data['candidate'][field]=value
    with pytest.raises(ValueError):ModelEvaluationEligibility.model_validate(data)


def test_summary_plan_rewrite_breaks_original_calibration_partition(metadata):
    data=declare(metadata).model_dump();candidate=data['candidate'];candidate['training_plan']['plan_id']='rewritten'
    candidate['training_plan_digest']=TemporalPreparationPlan.model_validate(candidate['training_plan']).digest
    with pytest.raises(ValueError):ModelEvaluationEligibility.model_validate(data)


def test_legacy_manifest_bearing_wire_form_is_not_accepted(metadata):
    result=declare(metadata);legacy=result.model_dump()
    legacy.pop('candidate');legacy.update(version='1.0',candidate_manifest_json=metadata['bundle'].manifest.model_dump_json())
    with pytest.raises(ValueError):ModelEvaluationEligibility.model_validate(legacy)


def _genuine_arguments(bundle):
    """Projection inputs from a genuine D2 owner; no evaluator/authority issuance."""
    m=bundle.manifest;p=m.provenance;s=p.states[0];schema=FeatureSchemaCompatibility.model_validate_json(s.representation_json)
    cfg=schema.configuration
    cap=FittedCandidateCapabilities(candidate_bundle_id=bundle.bundle_id,artifact_digest=m.artifact_digest,
        training_handoff_id=p.handoff_id,configuration_digest=p.configuration_digest,fitted_state_id=s.state.state_id,
        fitted_state_digest=m.state_digest,representation_schema_id=schema.schema_id,feature_names=m.feature_names,feature_dtypes=m.feature_dtypes)
    ds=p.plan.datasets[0];evaluation=TemporalDatasetBinding.model_validate({**ds.model_dump(),
        'reference':replace(ds.reference,attempt_id=UUID(int=888),version_id='8'*64),'manifest_digest':'8'*64,'admission_digest':'9'*64})
    dims={c.series_id:c.dimensions for c in p.labels};measurement=cfg.execution.measurement;temporal=cfg.execution.temporal
    relationship=PublicationRelationshipDeclaration(relationship_id='unissued-native-contract-fixture',authority_id='declaration-only',authority_version='1.0',
        evidence_references=('requires-independent-approval',),training=ds,evaluation=evaluation,metric=measurement.metric,
        unit=measurement.unit,currency=measurement.currency,frequency=temporal.frequency.value,calendar=temporal.calendar,
        timezone=temporal.timezone,period_anchor=temporal.period_anchor,
        series=tuple(SeriesEvaluationScope(series_id=sid,dimensions=dims[sid]) for sid in ds.series_ids))
    engine=CanonicalFeaturePipeline(cfg);fold=p.plan.folds[0]
    coverage=ValidationOnlyCoverage(fold_id=fold.fold_id,periods=tuple(ValidationH1Period(forecast_origin=o,target_period=o,
        target_period_end=engine._advance(engine._time(o),1)) for o in fold.validation_origins))
    return dict(capabilities=cap,relationship=relationship,coverage=coverage,runtime=m.runtime)


def test_genuine_candidates_are_label_free_and_remain_owned(tmp_path):
    from src.services.canonical_xgboost_training_service import CandidateTrainingError
    make=runpy.run_path('tests/training/test_canonical_xgboost_training.py')['make_owner'].__wrapped__(tmp_path)
    declarations=[];labels=[]
    def change_targets(raw):
        dates=sorted(raw['from'].unique())
        raw.loc[raw['from'].isin(dates[4:6]),'reported']+=4.
    for mutation in (None,change_targets):
        b=make(rows_change=mutation);bundle=b['train']();m=bundle.manifest
        expected=tuple(float(v) for v in np.frombuffer(bytes.fromhex(m.predictions_float32_hex),dtype='<f4'))
        assert b['owner'].verify_candidate(bundle)==expected
        args=_genuine_arguments(bundle);result=declare_evaluation_eligibility(bundle,**args)
        assert_label_free_graph(result);assert_original_summary(result,bundle)
        assert result.status=='DECLARATION_ONLY' and result.authorization.issuance=='NOT_IMPLEMENTED'
        clone=CandidateBundle(bytes(bytearray(bundle.payload)))
        assert declare_evaluation_eligibility(clone,**args)==result  # Metadata comparison isn't loading permission.
        with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(clone)
        labels.append(tuple(np.frombuffer(bytes.fromhex(m.labels_float32_hex),dtype='<f4')))
        declarations.append(result)
    assert labels[0]==(16.,116.,32.,132.) and labels[1]==(20.,120.,36.,136.)
    assert declarations[0].candidate.manifest_digest!=declarations[1].candidate.manifest_digest
    assert declarations[0].candidate.candidate_bundle_id!=declarations[1].candidate.candidate_bundle_id


def test_provenance_summary_field_allowlist_is_explicit():
    assert set(LabelFreeCandidateProvenance.model_fields)=={
        'version','candidate_bundle_id','manifest_digest','artifact_digest','artifact_size','bundle_size',
        'model_family','serialization','model_input_policy','training_invocation','training_invocation_digest',
        'training_scope_id','training_scope_digest','label_authority_id','label_authority_version',
        'training_handoff_id','preparation_id','training_plan','training_plan_digest','configuration_digest',
        'fitted_state_id','fitted_state_digest','fitted_state_size','training_partition_reference',
        'training_schema_digest','representation','series','runtime_digest'}
    assert set(ModelEvaluationEligibility.model_fields)=={
        'version','status','candidate','capabilities','relationship','coverage','runtime','resources','authorization'}


@pytest.mark.parametrize('where',('plan','configuration','invocation'))
def test_secret_bearing_summary_revalidation_has_no_diagnostics(metadata,where,capsys,caplog):
    data=declare(metadata).model_dump();secret='SUMMARY_SECRET_931';candidate=data['candidate']
    if where=='plan':candidate['training_plan']['folds']=[{secret:secret}]
    elif where=='configuration':candidate['representation']['configuration']['configuration_id']={secret:secret}
    else:candidate['training_invocation']['invocation_id']={secret:secret}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(ValueError) as exc:ModelEvaluationEligibility.model_validate(data)
    output=capsys.readouterr()
    assert not caught and not output.err
    assert secret not in output.out+output.err+caplog.text+''.join(traceback.format_exception(exc.value))


_PUBLIC_VALIDATORS=('constructor','python','json','strings')
_DIAGNOSTIC_SCOPES=('root','capabilities','relationship','coverage','runtime','resources','authorization')


def _public_validate(contract,entry,data,**options):
    if entry=='constructor':return contract(**data)
    if entry=='python':return contract.model_validate(data,**options)
    if entry=='json':return contract.model_validate_json(json.dumps(data),**options)
    return contract.model_validate_strings(data,**options)


def _assert_fixed_diagnostics(operation,secret,capsys,caplog):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as error:operation()
    exc=error.value;captured=capsys.readouterr()
    assert exc.__cause__ is None and exc.__context__ is None
    assert exc.args==('invalid non-authoritative evaluation declaration',)
    text=str(exc)+repr(exc)+repr(exc.args)+''.join(traceback.format_exception(exc))
    assert secret not in text+captured.out+captured.err+caplog.text
    assert not caught and not captured.err
    with warnings.catch_warnings(record=True) as unrelated:
        warnings.simplefilter('always');warnings.warn('unrelated declaration warning',UserWarning)
    assert len(unrelated)==1


@pytest.mark.parametrize('entry',_PUBLIC_VALIDATORS)
@pytest.mark.parametrize('scope',_DIAGNOSTIC_SCOPES)
def test_public_deserialization_unknown_names_have_no_diagnostics(metadata,entry,scope,capsys,caplog):
    secret='UNKNOWN_'+UUID(bytes=__import__('os').urandom(16)).hex
    data=declare(metadata).model_dump(mode='json')
    target=data if scope=='root' else data[scope];target[secret]='VALUE_'+secret
    _assert_fixed_diagnostics(lambda:_public_validate(ModelEvaluationEligibility,entry,data),secret,capsys,caplog)


@pytest.mark.parametrize('entry',_PUBLIC_VALIDATORS)
@pytest.mark.parametrize('scope',_DIAGNOSTIC_SCOPES+('candidate','period','series'))
def test_nested_declarations_are_independently_sanitized(metadata,entry,scope,capsys,caplog):
    root=declare(metadata)
    obj=(root if scope=='root' else root.coverage.periods[0] if scope=='period'
         else root.relationship.series[0] if scope=='series' else getattr(root,scope))
    secret='NESTED_'+UUID(bytes=__import__('os').urandom(16)).hex
    data=obj.model_dump(mode='json');data[secret]={'VALUE_'+secret:secret}
    _assert_fixed_diagnostics(lambda:_public_validate(type(obj),entry,data),secret,capsys,caplog)


@pytest.mark.parametrize('entry',_PUBLIC_VALIDATORS)
@pytest.mark.parametrize('field',('candidate','capabilities','relationship','coverage','runtime','resources','authorization'))
def test_malformed_public_field_types_have_no_diagnostics(metadata,entry,field,capsys,caplog):
    secret='TYPE_'+UUID(bytes=__import__('os').urandom(16)).hex
    data=declare(metadata).model_dump(mode='json');data[field]={secret:secret}
    _assert_fixed_diagnostics(lambda:_public_validate(ModelEvaluationEligibility,entry,data),secret,capsys,caplog)


@pytest.mark.parametrize('entry',('python','json','strings'))
@pytest.mark.parametrize('override',('ignore','allow'))
def test_public_entry_points_cannot_disable_extra_rejection(entry,override,capsys,caplog):
    secret='EXTRA_'+UUID(bytes=__import__('os').urandom(16)).hex
    _assert_fixed_diagnostics(lambda:_public_validate(EvaluationResourcePolicy,entry,{secret:secret},extra=override),
                              secret,capsys,caplog)


@pytest.mark.parametrize('entry',('constructor','python','json'))
def test_valid_public_roundtrips_preserve_bytes_schema_and_state(metadata,entry):
    root=declare(metadata);result=_public_validate(ModelEvaluationEligibility,entry,root.model_dump(mode='json'))
    assert result.model_dump_json()==root.model_dump_json() and result.eligibility_id==root.eligibility_id
    assert_label_free_graph(result);assert_original_summary(result,metadata['bundle'])
    assert type(result.runtime) is not RuntimeIdentity  # D2 class is untouched.
    assert result.runtime.model_dump_json()==metadata['runtime'].model_dump_json()
    for name in _DIAGNOSTIC_SCOPES[1:]+('candidate',):
        obj=getattr(root,name);rebuilt=_public_validate(type(obj),entry,obj.model_dump(mode='json'))
        assert rebuilt.model_dump_json()==obj.model_dump_json()


def test_string_mode_valid_scalar_declaration_preserves_behavior():
    obj=EvaluationResourcePolicy.model_validate_strings({'max_origins':'2','max_native_calls':'2'})
    assert obj.max_origins==2 and obj.max_native_calls==2
    assert EvaluationResourcePolicy.model_validate_json(obj.model_dump_json()).model_dump_json()==obj.model_dump_json()


def test_compiled_nested_validation_and_subclass_dispatch_are_preserved(metadata,capsys,caplog):
    seen=[]
    class DerivedPolicy(EvaluationResourcePolicy):
        declared_flag: bool = True

        @model_validator(mode='after')
        def observe_context(self,info):
            seen.append(info.context);return self
    class NestedPolicy(EvaluationResourcePolicy):
        child: DerivedPolicy
    assert DerivedPolicy.__pydantic_custom_init__ is False
    assert NestedPolicy.model_validate({'child':{'max_origins':2}}).child.max_origins==2
    secret='COMPILED_'+UUID(bytes=__import__('os').urandom(16)).hex
    _assert_fixed_diagnostics(lambda:NestedPolicy.model_validate({'child':{secret:secret}}),secret,capsys,caplog)
    _assert_fixed_diagnostics(lambda:DerivedPolicy.model_validate({'max_origins':True}),secret,capsys,caplog)
    _assert_fixed_diagnostics(lambda:NestedPolicy.model_validate({'child':{'max_origins':'2'}},strict=True),secret,capsys,caplog)
    assert DerivedPolicy.model_validate({'declared_flag':'true'}).declared_flag is True
    _assert_fixed_diagnostics(lambda:NestedPolicy.model_validate({'child':{'declared_flag':'true'}},strict=True),secret,capsys,caplog)
    context={'identity':'owned-declaration'}
    assert DerivedPolicy.model_validate({'max_origins':2},strict=True,context=context).max_origins==2
    assert seen[-1] is context
    assert NestedPolicy.model_validate({'child':{'max_origins':2}},context=context).child.max_origins==2
    assert seen[-1] is context


@pytest.mark.parametrize('field',('capabilities','relationship','coverage','runtime','resources'))
def test_factory_failures_leave_no_original_exception_context(metadata,field,capsys,caplog):
    secret='FACTORY_'+UUID(bytes=__import__('os').urandom(16)).hex
    obj=metadata.get(field,EvaluationResourcePolicy())
    first=next(iter(type(obj).model_fields));bad=changed(obj,**{first:{secret:secret}})
    _assert_fixed_diagnostics(lambda:declare(metadata,**{field:bad}),secret,capsys,caplog)


def test_invalid_json_syntax_has_no_original_exception_context(capsys,caplog):
    secret='JSON_'+UUID(bytes=__import__('os').urandom(16)).hex
    _assert_fixed_diagnostics(lambda:EvaluationResourcePolicy.model_validate_json('{"'+secret+'":'),secret,capsys,caplog)


@pytest.mark.parametrize('field',('capabilities','relationship','coverage','runtime','resources'))
def test_factory_rejects_unchecked_extra_fields_before_serialization(metadata,field,capsys,caplog):
    secret='UNCHECKED_'+UUID(bytes=__import__('os').urandom(16)).hex
    obj=metadata.get(field,EvaluationResourcePolicy()).model_copy(update={secret:'VALUE_'+secret})
    assert secret in vars(obj)  # A schema serializer would otherwise silently drop it.
    _assert_fixed_diagnostics(lambda:declare(metadata,**{field:obj}),secret,capsys,caplog)


@pytest.mark.parametrize('entry',_PUBLIC_VALIDATORS)
def test_public_errors_detach_ambient_validation_exception(entry,capsys,caplog):
    secret='AMBIENT_'+UUID(bytes=__import__('os').urandom(16)).hex
    try:RuntimeIdentity.model_validate({secret:secret})  # Existing D2 API, outside D3's guard.
    except ValueError as ambient:
        assert secret in str(ambient)
        _assert_fixed_diagnostics(lambda:_public_validate(EvaluationResourcePolicy,entry,{secret:secret}),
                                  secret,capsys,caplog)
    else:pytest.fail('malformed runtime must reject')
