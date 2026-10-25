"""All fits are test doubles. Actual canonical provider/preprocessing is retained."""
import copy
import json
import runpy
import traceback
import warnings
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.training_adapter import TrainingSpecification,AdapterCapability,TrainingCandidateMetadata,capability
from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
from src.services.canonical_recurrent_preparation_service import CanonicalRecurrentPreparationService
from src.services.canonical_transformer_preparation_service import CanonicalTransformerPreparationService
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService,CandidateReference
from src.training.canonical_adapters import NativeBackend,FrameworkUnavailable


@pytest.fixture
def setup(tmp_path):
    build=runpy.run_path('tests/training/test_canonical_transformer_preparation.py')['bundle'].__wrapped__(tmp_path)
    cases=runpy.run_path('tests/training/test_canonical_transformer_preparation.py')
    def create(**kw):
        b=build(**kw)
        # Explicit operator feature configuration: original rolling outputs are
        # not generally exact float32. Do not silently drop/cast them in adapters.
        from src.data.contracts.features import FeatureConfiguration
        from src.data.contracts.temporal_preparation import TemporalPreparationPlan
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        cfg=b['configuration'].model_dump();names=('location','lag1','promotion')
        cfg['ordered_outputs']=names
        cfg['output_dtypes']={k:v for k,v in cfg['output_dtypes'].items() if k in names}
        cfg['missing_value_behavior']={k:v for k,v in cfg['missing_value_behavior'].items() if k in names}
        b['configuration']=FeatureConfiguration.model_validate(cfg)
        p=b['plan'].model_dump();p['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
        b['plan']=TemporalPreparationPlan.model_validate(p)
        b['recurrent']=CanonicalRecurrentPreparationService(b['tabular'])
        calls=[];state={'cancelled':False,'allowed':True,'failure':False,'unavailable':False,'during_fit':None}
        class Backend:
            kind='test_stub';version='bounded_fake_v1'
            def __init__(self,family):self.family=family;calls.append(('construct',family))
            def fit(self,batch,spec,checkpoint):
                calls.append(('fit',batch,spec));checkpoint()
                assert all(not a.flags.writeable for a in batch.streams)
                if state['during_fit']:state['during_fit']()
                if state['failure']:raise RuntimeError('PRIVATE_BACKEND_SECRET_7931')
                checkpoint();return object()
        def backend(family):
            if state['unavailable']:raise FrameworkUnavailable()
            return Backend(family)
        def approve(context):
            calls.append(('approve',context));return state['allowed']
        b.update(calls=calls,status=state)
        b['trainer']=CanonicalAdapterTrainingService(b['tabular'],b['recurrent'],b['transformer'],authorize_training=approve,backend_factory=backend)
        def run(family='xgboost',**kwargs):
            spec=kwargs.pop('specification',TrainingSpecification(family=family,input_dtype='float32' if family=='xgboost' else 'float64'))
            req=None
            if family in ('lstm','gru','transformer'):
                req=cases['request'](b,tensor_dtype=spec.input_dtype,scaling='identity_v1')
                if family!='transformer':
                    from src.preprocessing.canonical_recurrent import RecurrentPreparationRequest
                    req=RecurrentPreparationRequest(fold_id=req.fold_id,model_family=family,origins=req.origins,
                        feature_names=('location','lag1'),window=req.window,tensor_dtype=req.tensor_dtype,scaling='identity_v1')
            return b['trainer'].train(spec,plan=b['plan'],feature_configuration=b['configuration'],preparation_request=kwargs.pop('preparation_request',req),
                cancelled=kwargs.pop('cancelled',lambda:state['cancelled']),**kwargs)
        b['run']=run;return b
    return create


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_all_families_real_preparation_training_only_and_private_candidate(setup,family):
    b=setup();result=b['run'](family)
    assert result.status=='SUCCESS'
    fits=[c for c in b['calls'] if c[0]=='fit'];assert len(fits)==1
    batch,spec=fits[0][1:]
    assert batch.family==family and result.metadata.specification.family==family
    assert batch.targets.shape==(4 if family in ('xgboost','lightgbm') else 2,1)
    assert result.metadata.backend=='test_stub' and result.metadata.classification=='CANDIDATE_ONLY'
    assert b['trainer'].owns(result.candidate)
    assert not b['trainer'].owns(copy.copy(result.candidate)) and not b['trainer'].owns(CandidateReference())
    assert not copy.copy(b['trainer']).owns(result.candidate)
    assert not hasattr(result.candidate,'model') and not hasattr(result.candidate,'artifact')
    loaded=TrainingCandidateMetadata.model_validate_json(result.metadata.canonical_json)
    assert loaded.candidate_id==result.metadata.candidate_id
    if family in ('xgboost','lightgbm'):
        assert batch.streams[0].shape[0]==4 and batch.targets[:,0].tolist()==[16.,116.,32.,132.]
    else:
        assert batch.lengths.tolist()==[1,1] and batch.padding.tolist()==[[True,True,False]]*2
        assert batch.targets[:,0].tolist()==[32.,132.]
        assert batch.offsets==(-3,-2,-1)
    assert b['trainer'].discard(result.candidate) and not b['trainer'].owns(result.candidate)


@pytest.mark.parametrize('family',['secret-unknown',None,False,{},'catboost'])
def test_unsupported_family_before_any_input_access(setup,monkeypatch,family):
    b=setup();spy=Mock(side_effect=AssertionError('provider touched'))
    monkeypatch.setattr(b['tabular'],'prepare',spy)
    out=b['trainer'].train({'family':family},plan=object(),feature_configuration=object())
    assert out.status=='UNSUPPORTED_FAMILY' and not b['calls'];spy.assert_not_called()


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_missing_framework_is_explicit_without_fallback(setup,family):
    b=setup();b['status']['unavailable']=True
    out=b['run'](family)
    assert out.status=='FRAMEWORK_UNAVAILABLE' and out.candidate is None
    assert not any(c[0]=='fit' for c in b['calls'])


def test_actual_native_loader_reports_missing_dependency(monkeypatch):
    import src.training.canonical_adapters as adapters
    loader=Mock(side_effect=ModuleNotFoundError('PRIVATE_DEPENDENCY_SECRET'));monkeypatch.setattr(adapters.importlib,'import_module',loader)
    with pytest.raises(FrameworkUnavailable):NativeBackend('lightgbm')
    loader.assert_called_once_with('lightgbm')


@pytest.mark.parametrize('family',['xgboost','lightgbm'])
def test_actual_tree_backend_configuration_with_stub_estimators(setup,monkeypatch,family):
    b=setup();captured=[]
    class Estimator:
        def __init__(self,**params):captured.append(params)
        def fit(self,x,y):
            captured.append((x.copy(),y.copy()));assert x.dtype==np.float32 if family=='xgboost' else x.dtype==np.float64
            return self
    fake=SimpleNamespace(__version__='2.0.0' if family=='xgboost' else '4.0.0',XGBRegressor=Estimator,LGBMRegressor=Estimator)
    import src.training.canonical_adapters as adapters
    monkeypatch.setattr(adapters.importlib,'import_module',lambda name:fake)
    b['trainer']._backend_factory=NativeBackend  # trusted test composition only
    result=b['run'](family)
    assert result.status=='SUCCESS' and captured[0]['n_jobs']==1 and captured[0]['random_state']==42
    assert captured[1][1].dtype==np.float32


@pytest.mark.parametrize('fault',['wrong_family','dtype','wrong_request'])
def test_neural_capability_mismatch_before_provider(setup,monkeypatch,fault):
    b=setup();spy=Mock(side_effect=AssertionError('no capture'));monkeypatch.setattr(b['recurrent'],'prepare',spy)
    cases=runpy.run_path('tests/training/test_canonical_recurrent_preparation.py')
    req=cases['request'](b,scaling='identity_v1',tensor_dtype='float64',model_family='lstm' if fault!='wrong_family' else 'gru')
    if fault=='wrong_request':req=object()
    spec=TrainingSpecification(family='lstm',input_dtype='float32' if fault=='dtype' else 'float64')
    out=b['trainer'].train(spec,plan=b['plan'],feature_configuration=b['configuration'],preparation_request=req)
    assert out.status=='FAILED';spy.assert_not_called();assert not b['calls']


@pytest.mark.parametrize('boundary',['before','during_fit','denied','late_denied','failure','callback_error'])
def test_failure_cancellation_and_authorization_no_partial_candidate(setup,boundary):
    b=setup()
    if boundary=='before':b['status']['cancelled']=True
    elif boundary=='during_fit':b['status']['during_fit']=lambda:b['status'].update(cancelled=True)
    elif boundary=='denied':b['status']['allowed']=False
    elif boundary=='late_denied':b['status']['during_fit']=lambda:b['status'].update(allowed=False)
    elif boundary=='failure':b['status']['failure']=True
    else:b['trainer']._authorize=Mock(side_effect=RuntimeError('PRIVATE_CALLBACK_SECRET'))
    out=b['run']();assert out.status!='SUCCESS' and out.candidate is None and out.metadata is None
    assert not b['trainer']._candidates
    if boundary in ('before','denied','callback_error'):assert not any(c[0]=='construct' for c in b['calls'])


@pytest.mark.parametrize('resource,value,family',[('max_rows',1,'xgboost'),('max_cells',1,'gru'),('max_bytes',1,'xgboost'),
    ('max_parameters',1,'lstm'),('max_tree_nodes',1,'lightgbm'),('max_work_units',1,'transformer')])
def test_resource_admission_before_backend(setup,resource,value,family):
    b=setup();spec=TrainingSpecification(family=family,input_dtype='float32' if family=='xgboost' else 'float64',resources={resource:value})
    out=b['run'](family,specification=spec)
    assert out.status=='RESOURCE_LIMIT' and not any(c[0]=='construct' for c in b['calls'])


def test_reentrant_training_is_denied_without_second_preparation(setup):
    b=setup();reentries=[]
    def reenter():reentries.append(b['run']().status)
    b['status']['during_fit']=reenter
    assert b['run']().status=='SUCCESS' and reentries==['BUSY']
    assert len([c for c in b['calls'] if c[0]=='fit'])==1


def test_metadata_determinism_and_validation_test_payload_isolation(setup,monkeypatch):
    from dataclasses import replace
    b=setup();first=b['run']();original=b['tabular'].prepare
    class Forbidden:
        def __iter__(self):raise AssertionError('held-out payload visited')
    def poison(*args,**kwargs):
        source=original(*args,**kwargs)
        outputs=tuple(o if o.representation.binding.partition=='training' else o.model_copy(update={'values':Forbidden()}) for o in source.outputs)
        targets=tuple(y if o.representation.binding.partition=='training' else Forbidden() for o,y in zip(source.outputs,source.targets))
        return replace(source,outputs=outputs,targets=targets)
    monkeypatch.setattr(b['tabular'],'prepare',poison)
    second=b['run']()
    assert second.status=='SUCCESS' and first.metadata.candidate_id==second.metadata.candidate_id


@pytest.mark.parametrize('fault',['state','geometry','targets','boolean','nonfinite','lossy_target'])
def test_invalid_prepared_training_values_reject_before_backend(setup,monkeypatch,fault):
    from dataclasses import replace
    b=setup();original=b['tabular'].prepare
    def bad(*a,**kw):
        source=original(*a,**kw);outputs=list(source.outputs);targets=list(source.targets)
        if fault=='targets':targets[0]=targets[0][:-1]
        elif fault=='lossy_target':targets[0]=(0.1,*targets[0][1:])
        else:
            raw=outputs[0].model_dump()
            if fault=='state':raw['state']['fold_id']='substituted'
            elif fault=='geometry':raw['values']=raw['values'][:-1]
            else:raw['values']=tuple((True if fault=='boolean' else float('inf'),*row[1:]) for row in raw['values'])
            # Simulate a compromised handoff; execution must revalidate, not trust
            # a typed class identity or model_copy shortcut.
            update={'state':outputs[0].state.model_copy(update={'fold_id':'substituted'})} if fault=='state' else {'values':raw['values']}
            outputs[0]=outputs[0].model_copy(update=update)
        return replace(source,outputs=tuple(outputs),targets=tuple(targets))
    monkeypatch.setattr(b['tabular'],'prepare',bad)
    out=b['run']();assert out.status=='FAILED' and not any(c[0]=='construct' for c in b['calls'])


@pytest.mark.parametrize('field,value',[('seed',True),('input_dtype','float16'),('steps',False),('learning_rate',float('nan'))])
def test_contract_strictness(field,value):
    raw=dict(family='gru');raw[field]=value
    with pytest.raises(EvaluationDeclarationError):TrainingSpecification.model_validate(raw)


def test_incompatible_capability_and_precision_reject():
    with pytest.raises(EvaluationDeclarationError):TrainingSpecification(family='xgboost',input_dtype='float64')
    raw=capability('gru').model_dump();raw['representation']='tabular'
    with pytest.raises(EvaluationDeclarationError):AdapterCapability.model_validate(raw)


def test_secret_inputs_and_errors_are_sanitized(setup,capsys,caplog):
    b=setup();secret='M6_PRIVATE_FIELD_57291'
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as exc:TrainingSpecification.model_validate_json(json.dumps({'family':'gru',secret:secret}))
        e=exc.value
        assert e.__cause__ is None and e.__context__ is None
        assert secret not in repr(e)+str(e)+repr(e.args)+''.join(traceback.format_exception(e))
        result=b['trainer'].train({'family':'gru',secret:secret},plan=object())
    assert result.status=='FAILED' and secret not in repr(result)+capsys.readouterr().err+caplog.text and not seen


def test_framework_free_import_in_fresh_process():
    import subprocess,sys,os
    code='''
import sys,importlib.abc
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in ('torch','tensorflow','xgboost','lightgbm','mlflow','sqlalchemy','joblib'):raise AssertionError(name)
sys.meta_path.insert(0,Deny())
import src.services.canonical_adapter_training_service
import src.ml.models.model_factory
import src.ml.models.xgboost_model
'''
    r=subprocess.run([sys.executable,'-c',code],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='.'),capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr


@pytest.mark.parametrize('family',['lightgbm','lstm','gru','transformer','catboost',None,{}])
def test_legacy_unsupported_family_before_dependency_loading(monkeypatch,family):
    from src.ml.models import model_factory
    loader=Mock(side_effect=AssertionError('legacy dependency touched'));monkeypatch.setattr(model_factory,'_load_model_class',loader)
    with pytest.raises(ValueError):model_factory.get_model(family)
    loader.assert_not_called()


@pytest.mark.parametrize('method',['save','load'])
@pytest.mark.parametrize('suffix',['.pkl','.joblib','.json','.ubj'])
def test_legacy_persistence_rejects_before_io(monkeypatch,tmp_path,method,suffix):
    from src.ml.models.xgboost_model import XGBoostForecastModel
    model=object.__new__(XGBoostForecastModel)
    model.model=SimpleNamespace(load_model=Mock(side_effect=AssertionError('no load')),save_model=Mock(side_effect=AssertionError('no save')))
    path=tmp_path/('candidate'+suffix)
    with pytest.raises(ValueError,match='legacy persistence unsupported'):getattr(model,method)(path)
    assert not path.exists();model.model.load_model.assert_not_called();model.model.save_model.assert_not_called()


def test_trusted_bootstrap_and_foreign_composition_reject(setup):
    a=setup();b=setup()
    with pytest.raises(EvaluationDeclarationError):
        CanonicalAdapterTrainingService(a['tabular'],a['recurrent'],a['transformer'],authorize_training=True)
    with pytest.raises(EvaluationDeclarationError):
        CanonicalAdapterTrainingService(a['tabular'],b['recurrent'],a['transformer'],authorize_training=lambda *_:True)


def test_no_preprocessing_refit_during_adapter_and_public_report(setup,monkeypatch):
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    from src.data.contracts.training_adapter import TrainingReport
    b=setup();fits=[];original=CanonicalFeaturePipeline.fit_state
    def fit(self,frame,**kwargs):fits.append(1);return original(self,frame,**kwargs)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    b['status']['during_fit']=lambda:monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('adapter must not refit')))
    out=b['run']('transformer');assert out.status=='SUCCESS' and len(fits)==1
    report=TrainingReport.model_validate_json(out.public_report.canonical_json)
    assert report==out.public_report and report.metadata.target_shape==(2,1)
    assert not hasattr(report,'candidate') and not hasattr(report,'model')
    record=b['trainer']._candidates[id(out.candidate)]
    assert record[4].state_id==report.metadata.fitted_state_id


def test_live_candidate_bound_admits_no_new_work(setup,monkeypatch):
    b=setup();b['trainer']._candidates={i:None for i in range(8)}
    spy=Mock(side_effect=AssertionError('no data read at capacity'));monkeypatch.setattr(b['tabular'],'prepare',spy)
    assert b['run']().status=='RESOURCE_LIMIT';spy.assert_not_called();assert not b['calls']


def test_elapsed_boundary_and_preparation_cancellation(setup,monkeypatch):
    b=setup();ticks=[0]
    import src.services.canonical_adapter_training_service as module
    monkeypatch.setattr(module,'monotonic',lambda:ticks[0])
    original=b['tabular'].prepare
    def prepare(*a,**kw):ticks[0]=30;return original(*a,**kw)
    monkeypatch.setattr(b['tabular'],'prepare',prepare)
    out=b['run']();assert out.status=='CANCELLED_OR_EXPIRED' and not b['calls']


def test_lossy_canonical_features_reject_without_silent_subset_or_cast(setup):
    b=setup()
    # Restore the complete original operator configuration, whose rolling mean
    # includes nonrepresentable float32 values. The adapter cannot drop it.
    cases=runpy.run_path('tests/training/test_canonical_transformer_preparation.py')
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as p:
        other=cases['bundle'].__wrapped__(Path(p))()
        result=b['trainer'].train(TrainingSpecification(family='xgboost'),plan=other['plan'],feature_configuration=other['configuration'])
    assert result.status=='FAILED' and not any(c[0]=='construct' for c in b['calls'])


@pytest.mark.parametrize('value',[True,0,-1,100000001])
def test_resource_numeric_coercion_rejects(value):
    with pytest.raises(EvaluationDeclarationError):TrainingSpecification(family='gru',resources={'max_work_units':value})


@pytest.mark.parametrize('family,version',[('xgboost','1.7.6'),('lightgbm','3.3.5'),('gru','1.13.0'),('transformer','unknown')])
def test_unsupported_framework_api_version_is_unavailable(monkeypatch,family,version):
    import src.training.canonical_adapters as adapters
    monkeypatch.setattr(adapters.importlib,'import_module',lambda name:SimpleNamespace(__version__=version))
    with pytest.raises(FrameworkUnavailable):NativeBackend(family)


@pytest.mark.parametrize('family',['lstm','gru','transformer'])
def test_actual_neural_mask_and_position_adapter_without_framework(setup,family):
    from src.training.canonical_adapters import _neural_input
    b=setup();assert b['run'](family).status=='SUCCESS'
    batch=next(c[1] for c in b['calls'] if c[0]=='fit')
    original=batch.streams[0].copy();x=_neural_input(batch);width=original.shape[-1]
    assert x.dtype==original.dtype and x.shape==(*original.shape[:2],2*width+1)
    assert np.array_equal(x[:,:,:width],np.where(batch.missing,0,original))
    assert np.array_equal(x[:,:,width:2*width],batch.missing.astype(original.dtype))
    assert x[0,:,-1].tolist()==[-3.,-2.,-1.]
    assert np.array_equal(batch.streams[0],original)
    assert not batch.streams[0].flags.writeable


def test_neural_only_option_cannot_be_silently_ignored_by_tree_adapter():
    with pytest.raises(EvaluationDeclarationError):TrainingSpecification(family='xgboost',hidden_size=16)


def test_malformed_nested_configuration_has_no_serializer_warning(setup,capsys,caplog):
    from uuid import uuid4
    b=setup();secret='M6_NESTED_'+uuid4().hex
    cfg=b['configuration'];ex=cfg.execution
    malformed=ex.measurement.model_copy(update={'unit':{secret:secret}})
    cfg=cfg.model_copy(update={'execution':ex.model_copy(update={'measurement':malformed})})
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        out=b['trainer'].train(TrainingSpecification(family='xgboost'),plan=b['plan'],feature_configuration=cfg)
    assert out.status=='FAILED' and not b['calls']
    assert secret not in repr(out)+out.public_report.canonical_json+capsys.readouterr().err+caplog.text
    assert not seen


@pytest.mark.parametrize('field',['plan','feature_configuration','preparation_request'])
def test_wrong_ingress_type_never_invokes_caller_serializer(setup,field):
    b=setup();cases=runpy.run_path('tests/training/test_canonical_recurrent_preparation.py')
    req=cases['request'](b,model_family='lstm',tensor_dtype='float64')
    touched=[]
    class Hostile:
        def __getattr__(self,name):touched.append(name);raise AssertionError('serializer must not be invoked')
    args=dict(plan=b['plan'],feature_configuration=b['configuration'],preparation_request=req)
    args[field]=Hostile()
    out=b['trainer'].train(TrainingSpecification(family='lstm',input_dtype='float64'),**args)
    assert out.status=='FAILED' and not touched and not b['calls']


def test_equivalent_utc_origins_preserve_non_utc_calendar_admission(setup):
    from datetime import timezone
    from src.data.contracts.temporal_preparation import TemporalPreparationPlan,TemporalFold
    b=setup(timezone='America/New_York')
    fold=b['plan'].folds[0].model_dump()
    for partition in ('training','validation','test'):
        fold[partition+'_origins']=tuple(o.astimezone(timezone.utc) for o in fold[partition+'_origins'])
    plan=b['plan'].model_dump();plan['folds']=(TemporalFold.model_validate(fold),)
    b['plan']=TemporalPreparationPlan.model_validate(plan)
    result=b['run']()
    assert result.status=='SUCCESS' and result.metadata.plan_digest==b['plan'].digest
    assert result.metadata.training_rows==4
    assert len([c for c in b['calls'] if c[0]=='fit'])==1
