"""R7-05A declaration-only inspection; scope from local owner fixtures; no runtime evidence claimed."""
from copy import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID
import json
import runpy
import traceback
from unittest.mock import Mock
import pytest

from src.data.contracts.canonical_forecast_persistence import (
    ForecastProvenance,CanonicalH1ForecastDeclaration,ForecastDeclarationBatch,
    ForecastPersistenceInspectionOutcome,compare_forecast_declarations)
from src.services.canonical_forecast_persistence_inspection_service import (
    CanonicalForecastPersistenceInspectionService,ForecastPersistenceInspectionError)


@pytest.fixture
def setup(tmp_path):
    prior=runpy.run_path('tests/runtime/test_canonical_h1_predictor.py')
    generator=prior['setup'].__wrapped__(tmp_path);make=next(generator)
    def build(family='xgboost'):
        b=make(family);p=b['prediction_request']
        d=CanonicalH1ForecastDeclaration(provenance=ForecastProvenance(prediction=p),series_id=p.inspection.snapshot_request.dataset.series_ids[0])
        s=CanonicalForecastPersistenceInspectionService(p,elapsed_clock=lambda:b['runtime_control']['elapsed'])
        b.update(declaration=d,persistence=s)
        b['inspect_declaration']=lambda:s.inspect(d,cancelled=lambda:b['runtime_control']['cancel'])
        return b
    yield build
    try:next(generator)
    except StopIteration:pass


@pytest.fixture(scope='module')
def template(tmp_path_factory):
    fixture=runpy.run_path('tests/runtime/test_canonical_h1_predictor.py')['setup'].__wrapped__(tmp_path_factory.mktemp('declaration-template'))
    b=next(fixture)()
    p=b['prediction_request']
    yield CanonicalH1ForecastDeclaration(provenance=ForecastProvenance(prediction=p),series_id=p.inspection.snapshot_request.dataset.series_ids[0])
    try:next(fixture)
    except StopIteration:pass


def denied(result):
    assert result.status=='DENIED' and result.state=='DECLARED'
    assert not result.writes_performed and not result.verified and not result.persistable and not result.executable_permission
    assert all(getattr(result,k) is None for k in ('declaration_id','logical_key_id','lineage_id','prediction_request_id'))


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_five_families_non_writing_structural_inspection(setup,monkeypatch,family):
    b=setup(family);trap=b['trap'](monkeypatch);d=b['declaration']
    result=b['inspect_declaration']()
    assert result.status=='UNAVAILABLE' and result.state=='DECLARED'
    assert (result.declaration_id,result.logical_key_id,result.lineage_id)==(d.declaration_id,d.logical_key_id,d.provenance.lineage_id)
    assert b['runtime_control']['captures']==0
    assert not result.writes_performed and not result.verified and not result.persistable and not result.executable_permission
    assert b['inspect_declaration']().canonical_bytes==result.canonical_bytes and b['runtime_control']['captures']==0
    assert ForecastPersistenceInspectionOutcome.model_validate_json(result.canonical_json)==result
    trap.assert_not_called()


@pytest.mark.parametrize('field,value',[
 ('state','VERIFIED'),('state','PERSISTABLE'),('prediction_value',0.),('prediction_value',-7.),
 ('prediction_value',True),('prediction_value',float('nan')),('prediction_value',float('inf')),
 ('prediction_value','SECRET_R705A_351'),('physical_dtype','float32'),('physical_dtype','float64'),
 ('physical_dtype','int64'),('physical_dtype',True),('native_artifact_id','f'*64),('loader_attestation_id','f'*64),
 ('executable_input_id','f'*64),('execution_authority_id','f'*64),('execution_evidence_id','f'*64),('supersedes','f'*64)])
def test_no_public_value_dtype_execution_claim_or_state_upgrade(template,field,value):
    with pytest.raises(ValueError) as caught:
        CanonicalH1ForecastDeclaration.model_validate_json(template.model_copy(update={field:value}).canonical_json)
    assert 'SECRET_R705A_351' not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('field',['writes_performed','verified','persistable','executable_permission','state'])
def test_outcome_cannot_claim_a_write_or_verification(field):
    data=dict(status='DENIED',reason='INVALID_DECLARATION_OR_DEPENDENCY');data[field]='PERSISTABLE' if field=='state' else True
    with pytest.raises(ValueError):ForecastPersistenceInspectionOutcome(**data)


def test_identity_roundtrip_key_partition_and_conflict_rules(template):
    d=template;parsed=CanonicalH1ForecastDeclaration.model_validate_json(d.canonical_json)
    assert parsed.canonical_bytes==d.canonical_bytes and parsed.declaration_id==d.declaration_id
    assert compare_forecast_declarations(d,parsed)=='IDENTICAL_DECLARATION'
    p=d.provenance.prediction;inner=p.inspection;r=inner.snapshot_request
    # Cutoff/publication are lineage, not logical-key partitions.
    for changed in (r.model_copy(update={'capture_cutoff':r.forecast_origin-timedelta(days=1)}),
                    r.model_copy(update={'dataset':r.dataset.model_copy(update={'admission_digest':'e'*64})})):
        other=d.model_copy(update={'provenance':d.provenance.model_copy(update={'prediction':p.model_copy(update={
            'inspection':inner.model_copy(update={'snapshot_request':changed})})})})
        other=CanonicalH1ForecastDeclaration.model_validate_json(other.canonical_json)
        assert other.logical_key_id==d.logical_key_id and other.provenance.lineage_id!=d.provenance.lineage_id
        assert compare_forecast_declarations(d,other)=='CONFLICTING_DECLARATION'
        with pytest.raises(ValueError):ForecastDeclarationBatch(declarations=(d,other))
    with pytest.raises(ValueError):ForecastDeclarationBatch(declarations=(d,d))
    second=d.model_copy(update={'series_id':r.dataset.series_ids[1]})
    assert compare_forecast_declarations(d,second)=='DIFFERENT_LOGICAL_KEY'
    ds=r.dataset.model_copy(update={'reference':replace(r.dataset.reference,organization_id=UUID(int=707))})
    tenant=d.model_copy(update={'provenance':d.provenance.model_copy(update={'prediction':p.model_copy(update={
        'inspection':inner.model_copy(update={'snapshot_request':r.model_copy(update={'dataset':ds})})})})})
    assert compare_forecast_declarations(d,tenant)=='DIFFERENT_LOGICAL_KEY'
    pair=tuple(sorted((d,second),key=lambda x:x.logical_key_id))
    assert len(ForecastDeclarationBatch(declarations=pair).declarations)==2
    with pytest.raises(ValueError):ForecastDeclarationBatch(declarations=tuple(reversed(pair)))


@pytest.mark.parametrize('fault',['candidate_id','package_id','representation_binding_id','frozen_state_id','snapshot_id','tenant','series','publication','origin','target'])
def test_lineage_scope_substitutions_reject(setup,fault):
    b=setup();d=b['declaration'];p=d.provenance.prediction;inner=p.inspection
    if fault in ('tenant','publication'):
        sr=inner.snapshot_request;ds=sr.dataset
        if fault=='tenant':ds=ds.model_copy(update={'reference':replace(ds.reference,organization_id=UUID(int=708))})
        else:ds=ds.model_copy(update={'admission_digest':'e'*64})
        inner=inner.model_copy(update={'snapshot_request':sr.model_copy(update={'dataset':ds})})
    elif fault=='origin':inner=inner.model_copy(update={'snapshot_request':inner.snapshot_request.model_copy(update={'forecast_origin':b['at'](6),'capture_cutoff':b['at'](6)})})
    elif fault=='target':p=p.model_copy(update={'target_period_end':b['at'](9)})
    elif fault=='series':d=d.model_copy(update={'series_id':UUID(int=709)})
    else:inner=inner.model_copy(update={fault:'e'*64})
    p=p.model_copy(update={'inspection':inner})
    if fault=='origin':p=p.model_copy(update={'target_period':b['at'](6),'target_period_end':b['at'](7)})
    d=d.model_copy(update={'provenance':d.provenance.model_copy(update={'prediction':p})})

    denied(b['persistence'].inspect(d))


@pytest.mark.parametrize('kind',['object','false','inspection_grant','prediction_unavailable','m9_approval'])
@pytest.mark.parametrize('argument',['execution_evidence','write_authority'])
def test_no_supplied_object_can_authorize_execution_or_writes(setup,monkeypatch,kind,argument):
    b=setup()
    token={'object':object(),'false':False,'inspection_grant':b['use_handle']}.get(kind)
    if kind=='prediction_unavailable':token=b['predict']()
    elif kind=='m9_approval':token=b['lifecycle'].require_further_validation_decision(b['life'])
    spy=Mock(side_effect=AssertionError('no writer or producer may be invoked'))
    monkeypatch.setattr(b['predictor'],'predict',spy)
    out=b['persistence'].inspect(b['declaration'],**{argument:token})
    denied(out);assert out.reason=='UNSUPPORTED_EXECUTION_OR_WRITE_AUTHORITY';spy.assert_not_called()


@pytest.mark.parametrize('fault',['cancel','deadline'])
def test_cancellation_and_deadline_without_partial_references(setup,fault):
    b=setup();d=b['declaration'];p=d.provenance.prediction
    p=p.model_copy(update={'limits':p.limits.model_copy(update={'max_elapsed_seconds':1})})
    d=d.model_copy(update={'provenance':d.provenance.model_copy(update={'prediction':p})})
    service=CanonicalForecastPersistenceInspectionService(p,elapsed_clock=lambda:b['runtime_control']['elapsed'])
    calls=[0]
    def stop():
        calls[0]+=1
        if calls[0]==2:
            if fault=='cancel':return True
            b['runtime_control']['elapsed']=1.
        return False
    result=service.inspect(d,cancelled=stop)
    denied(result);assert result.reason=='CANCELLED_OR_EXPIRED' and calls[0]==2


def test_service_rejects_fabricated_value_before_dependency(setup,monkeypatch,capsys,caplog,recwarn):
    b=setup();spy=Mock(side_effect=AssertionError('fabricated value must reject before inspection'))
    monkeypatch.setattr(b['predictor'],'predict',spy)
    denied(b['persistence'].inspect(b['declaration'].model_copy(update={'prediction_value':32.})))
    for data in ('{"state":"DECLARED","state":"SECRET_R705A_351"}',b'\xffSECRET_R705A_351','{"SECRET_R705A_351":'):
        with pytest.raises(ValueError) as error:CanonicalH1ForecastDeclaration.model_validate_json(data)
        assert 'SECRET_R705A_351' not in ''.join(traceback.format_exception(error.value))
    captured=capsys.readouterr();assert 'SECRET_R705A_351' not in captured.err+captured.out+caplog.text and not recwarn
    spy.assert_not_called()


def test_h1_is_exact_and_composition_cannot_use_detached_outcomes(setup):
    b=setup();d=b['declaration'];p=d.provenance.prediction
    for change in ({'horizon_step':2},{'target_period':b['at'](8)}):
        bad=d.model_copy(update={'provenance':d.provenance.model_copy(update={'prediction':p.model_copy(update=change)})})
        denied(b['persistence'].inspect(bad))
    for component in (None,b['predictor'],b['predict']()):
        with pytest.raises(ForecastPersistenceInspectionError) as error:CanonicalForecastPersistenceInspectionService(component)
        assert error.value.__cause__ is None and error.value.__context__ is None
    denied(copy(b['persistence']).inspect(d))


def test_no_runtime_reads_or_writes_even_with_revoked_inspection(setup,monkeypatch):
    b=setup();b['use_operator'].revoke(b['use_handle'])
    forbidden=Mock(side_effect=AssertionError('no runtime reads or writers may be invoked'))
    monkeypatch.setattr(b['predictor'],'predict',forbidden)
    monkeypatch.setattr(b['source_provider'],'open_read',forbidden)
    result=b['inspect_declaration']()
    assert result.status=='UNAVAILABLE' and not result.verified and not result.persistable
    assert result.prediction_request_id==b['prediction_request'].request_id
    assert b['runtime_control']['captures']==0
    forbidden.assert_not_called()
