"""R7-04A real owner-backed inspection, with estimator operations stubbed."""
from contextlib import contextmanager
from collections import Counter
from copy import copy
from datetime import timedelta
from unittest.mock import Mock
from uuid import UUID
from dataclasses import replace
import runpy
import traceback
import pytest

from src.data.contracts.canonical_h1_predictor import CanonicalH1PredictionRequest,CanonicalH1PredictionOutcome
from src.data.contracts.runtime_model_use import RuntimeUseLimits
from src.services.canonical_h1_predictor_service import CanonicalH1PredictorService,CanonicalH1PredictorError
from src.services.runtime_model_use_service import bootstrap_runtime_model_use,RuntimeInspectionCapability


@pytest.fixture
def setup(tmp_path):
    prior=runpy.run_path('tests/runtime/test_runtime_model_use.py')
    generator=prior['setup'].__wrapped__(tmp_path);make=next(generator)
    def build(family='xgboost'):
        b=make(family)
        req=CanonicalH1PredictionRequest(inspection=b['use_request'],target_period=b['at'](7),target_period_end=b['at'](8))
        service=CanonicalH1PredictorService(b['use_service'],elapsed_clock=lambda:b['runtime_control']['elapsed'])
        def trap(monkeypatch):
            forbidden=prior['trap'](b,monkeypatch)
            from src.preprocessing.canonical_tabular import FrozenTabularPreprocessor
            from src.preprocessing.canonical_recurrent import FrozenRecurrentPreprocessor
            from src.preprocessing.canonical_transformer import FrozenTransformerPreprocessor
            for cls in (FrozenTabularPreprocessor,FrozenRecurrentPreprocessor,FrozenTransformerPreprocessor):
                monkeypatch.setattr(cls,'transform',forbidden)
            return forbidden
        b.update(prediction_request=req,predictor=service,trap=trap)
        b['predict']=lambda:service.predict(req,inspection_capability=b['use_handle'],cancelled=lambda:b['runtime_control']['cancel'])
        return b
    yield build
    try:next(generator)
    except StopIteration:pass


def no_references(out):
    assert out.status=='DENIED'
    assert all(getattr(out,k) is None for k in ('request_id','scope_id','purpose_id','inspection_decision_id',
                                               'candidate_id','package_id','snapshot_id','family'))
    assert not out.executable_permission and not out.production_permission and not out.execution_performed


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_five_families_inspection_only_no_native_or_legacy(setup,monkeypatch,family):
    b=setup(family);forbidden=b['trap'](monkeypatch);reads=[];original=b['source_provider'].open_read
    @contextmanager
    def counted(*a,**kw):
        reads.append(1)
        with original(*a,**kw) as session:yield session;assert session.completed
    monkeypatch.setattr(b['source_provider'],'open_read',counted)
    out=b['predict']()
    assert out.status=='UNAVAILABLE' and out.family==family and len(reads)==2
    assert not out.executable_permission and not out.production_permission and not out.execution_performed
    assert (out.native_payload,out.native_loader,out.execution_authority,out.executable_inputs)==('UNAVAILABLE',)*4
    assert out.source_compatibility=='SOURCE_CONTEXT_ONLY' and out.evidence_classification=='TEST_ONLY_NON_DEPLOYABLE'
    req=b['prediction_request'];assert out.request_id==req.request_id and out.scope_id==req.scope_id and out.purpose_id==req.purpose_id
    assert out.snapshot_id==req.inspection.snapshot_id and out.candidate_id==req.inspection.candidate_id
    assert b['predict']().canonical_bytes==out.canonical_bytes and len(reads)==4
    assert CanonicalH1PredictionRequest.model_validate_json(req.canonical_json).request_id==req.request_id
    assert CanonicalH1PredictionOutcome.model_validate_json(out.canonical_json)==out
    assert not hasattr(out,'predictions')
    forbidden.assert_not_called()


@pytest.mark.parametrize('field',['candidate_id','package_id','family','representation_binding_id','frozen_state_id',
                                 'snapshot_id','tenant','series','publication','origin','cutoff'])
def test_substitutions_cannot_widen_original_scope(setup,field):
    b=setup();inner=b['use_request'];digest='f'*64
    if field in ('tenant','series','publication','origin','cutoff'):
        sr=inner.snapshot_request
        if field=='origin':sr=sr.model_copy(update={'forecast_origin':b['at'](6),'capture_cutoff':b['at'](6)})
        elif field=='cutoff':sr=sr.model_copy(update={'capture_cutoff':b['at'](8)})
        else:
            ds=sr.dataset
            if field=='tenant':ds=ds.model_copy(update={'reference':replace(ds.reference,organization_id=UUID(int=777))})
            elif field=='series':ds=ds.model_copy(update={'series_ids':tuple(reversed(ds.series_ids))})
            else:ds=ds.model_copy(update={'admission_digest':digest})
            sr=sr.model_copy(update={'dataset':ds})
        inner=inner.model_copy(update={'snapshot_request':sr})
    else:inner=inner.model_copy(update={field:'gru' if field=='family' else digest})
    req=b['prediction_request'].model_copy(update={'inspection':inner})
    if field=='origin':req=req.model_copy(update={'target_period':b['at'](6),'target_period_end':b['at'](7)})
    try:
        handle,_=b['use_operator'].grant_inspection(inner,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
    except ValueError:handle=b['use_handle']  # invalid declarations reject before any grant can issue
    no_references(b['predictor'].predict(req,inspection_capability=handle))


@pytest.mark.parametrize('kind',['missing','revoked','expired','forged','receipt','m9_actor'])
def test_inspection_authority_failures(setup,kind):
    b=setup();handle=b['use_handle']
    if kind=='missing':handle=None
    elif kind=='revoked':b['use_operator'].revoke(handle)
    elif kind=='expired':b['time']['now']+=timedelta(seconds=120)
    elif kind=='forged':handle=RuntimeInspectionCapability()
    elif kind=='receipt':handle=b['receipt']
    else:b['operator'].revoke_actor(b['principal'])
    no_references(b['predictor'].predict(b['prediction_request'],inspection_capability=handle))


@pytest.mark.parametrize('kind',['object','false','inspection_capability','inspection_receipt','inspection_decision','m9_approval'])
def test_no_object_is_an_execution_capability(setup,monkeypatch,kind):
    b=setup()
    if kind=='object':token=object()
    elif kind=='false':token=False
    elif kind=='inspection_capability':token=b['use_handle']
    elif kind=='inspection_receipt':token=b['receipt']
    elif kind=='inspection_decision':token=b['decide_use']()
    else:token=b['lifecycle'].require_further_validation_decision(b['life'])
    spy=Mock(side_effect=AssertionError('unsupported execution objects must not be used'))
    monkeypatch.setattr(b['use_service'],'decide',spy)
    out=b['predictor'].predict(b['prediction_request'],inspection_capability=b['use_handle'],execution_authority=token)
    no_references(out);assert out.reason=='EXECUTION_AUTHORITY_UNSUPPORTED';spy.assert_not_called()


@pytest.mark.parametrize('kind',['cancelled','outer_clock','wall_clock'])
def test_final_callback_revocation_not_weakened_by_predictor_wrapper(setup,monkeypatch,kind):
    b=setup('transformer');forbidden=b['trap'](monkeypatch);counts=Counter();events=[];arm={'target':None};holder={}
    def edge(name):
        counts[name]+=1;events.append((name,counts[name]))
        if arm['target']==(name,counts[name]):holder['operator'].revoke(holder['handle'])
    def wall():edge('wall_clock');return b['time']['now']
    def elapsed():edge('outer_clock');return 0.
    def cancelled():edge('cancelled');return False
    op,inspector=bootstrap_runtime_model_use(b['use_provider'],host_context_id=b['use_request'].host_context_id,clock=wall,elapsed_clock=lambda:0.)
    handle,_=op.grant_inspection(b['use_request'],valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
    holder.update(operator=op,handle=handle);predictor=CanonicalH1PredictorService(inspector,elapsed_clock=elapsed)
    counts.clear();events.clear()
    assert predictor.predict(b['prediction_request'],inspection_capability=handle,cancelled=cancelled).status=='UNAVAILABLE'
    trace=tuple(events);target=next(e for e in reversed(trace) if e[0]==kind)
    counts.clear();events.clear();arm['target']=target
    no_references(predictor.predict(b['prediction_request'],inspection_capability=handle,cancelled=cancelled))
    assert counts[kind]==target[1] and tuple(events[:trace.index(target)+1])==trace[:trace.index(target)+1]
    forbidden.assert_not_called()


@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_outer_limits_and_cancellation_at_terminal_capture(setup,mode):
    b=setup();req=b['prediction_request'].model_copy(update={'limits':RuntimeUseLimits(max_elapsed_seconds=1)})
    def final():
        if b['runtime_control']['captures']==2:
            if mode=='cancel':b['runtime_control']['cancel']=True
            else:b['runtime_control']['elapsed']=1.
    b['runtime_control']['on_return']=final
    out=b['predictor'].predict(req,inspection_capability=b['use_handle'],cancelled=lambda:b['runtime_control']['cancel'])
    no_references(out);assert out.reason=='CANCELLED_OR_EXPIRED'


@pytest.mark.parametrize('fault',['h2','target','end','purpose','authority_requirement','input_requirement','request_size','output_size'])
def test_contract_and_budget_admission(setup,fault):
    b=setup();req=b['prediction_request'];change={}
    if fault=='h2':change={'horizon_step':2}
    elif fault=='target':change={'target_period':b['at'](8)}
    elif fault=='end':change={'target_period_end':b['at'](9)}
    elif fault=='purpose':change={'execution_purpose':'production_inference'}
    elif fault=='authority_requirement':change={'execution_authority_requirement':'inspection_is_enough'}
    elif fault=='input_requirement':change={'executable_input_requirement':'source_context_is_tensor'}
    else:change={'limits':req.limits.model_copy(update={'max_request_bytes' if fault=='request_size' else 'max_output_bytes':1})}
    no_references(b['predictor'].predict(req.model_copy(update=change),inspection_capability=b['use_handle']))


def test_no_raw_json_ambiguity_secret_errors_or_positive_outcome(setup,capsys,caplog,recwarn):
    b=setup()
    for raw in ('{"version":"1.0","version":"SECRET_R704A_731"}',b'\xffSECRET_R704A_731','{"SECRET_R704A_731":'):
        with pytest.raises(ValueError) as caught:CanonicalH1PredictionRequest.model_validate_json(raw)
        assert 'SECRET_R704A_731' not in ''.join(traceback.format_exception(caught.value))
    def failure():raise RuntimeError('SECRET_R704A_731')
    no_references(b['predictor'].predict(b['prediction_request'],inspection_capability=b['use_handle'],cancelled=failure))
    captured=capsys.readouterr();assert 'SECRET_R704A_731' not in captured.out+captured.err+caplog.text and not recwarn
    for field in ('executable_permission','production_permission','execution_performed'):
        with pytest.raises(ValueError):CanonicalH1PredictionOutcome(status='DENIED',reason='INSPECTION_DENIED',**{field:True})
    with pytest.raises(ValueError):CanonicalH1PredictionOutcome(status='SUCCESS',reason='INSPECTION_DENIED')


def test_constructor_and_copied_composition_fail_closed(setup):
    b=setup()
    for value in (None,object(),copy(b['use_service']),b['decide_use']()):
        with pytest.raises(CanonicalH1PredictorError) as caught:CanonicalH1PredictorService(value)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    no_references(copy(b['predictor']).predict(b['prediction_request'],inspection_capability=b['use_handle']))
