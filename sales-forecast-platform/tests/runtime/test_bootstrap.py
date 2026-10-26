"""Runtime characterization; all operational edges are stubs or trapped."""
import ast
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = dict(Date='2020-01-01', Store=1, Dept=2, Type='A', Size=1,
               Temperature=1., Fuel_Price=1., CPI=1., Unemployment=1.)
ROUTES = {('GET','/health'), ('POST','/predict'), ('POST','/batch_predict'),
          ('POST','/forecast/week'), ('POST','/forecast'), ('POST','/forecast/async'),
          ('GET','/task/{task_id}'), ('GET','/model/stats'), ('GET','/dashboard/accuracy'),
          ('GET','/dashboard/worst-forecasts'), ('GET','/dashboard/insights'),
          ('POST','/dashboard/report'), ('GET','/monitoring/health'),
          ('GET','/metrics'), ('GET','/dashboard/job-history')}
JOBS = {'forecast_job':60, 'accuracy_job':10, 'report_job':120, 'production_model_sync_job':5}


def isolated_function(name):
    tree=ast.parse((ROOT/'api/main.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    function.decorator_list=[]
    return ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))


def test_characterize_route_catalog_without_operational_imports():
    found=set()
    for n in ast.walk(ast.parse((ROOT/'api/main.py').read_text())):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('get','post') and n.args and isinstance(n.args[0],ast.Constant):
            found.add((n.func.attr.upper(),n.args[0].value))
    assert found==ROUTES


def test_characterize_batch_rows_items_defect_preserved():
    from api.schemas import BatchPredictRequest,BatchPredictResponse
    import pandas as pd
    request=BatchPredictRequest(rows=[PAYLOAD]);service=Mock()
    scope={'pd':pd,'BatchPredictRequest':BatchPredictRequest,'BatchPredictResponse':BatchPredictResponse,
           'forecast_service':service,'_dependency':lambda name:service}
    exec(compile(isolated_function('batch_predict'),'api/main.py','exec'),scope)
    with pytest.raises(AttributeError,match='items'):scope['batch_predict'](request)
    assert not service.mock_calls


def test_characterize_response_requirements_and_active_forecast_schema():
    from api.schemas import BatchPredictResponse,ForecastRequest,ForecastItem,ForecastResponse
    with pytest.raises(ValidationError):BatchPredictResponse(predictions=[1.],model_version='production')
    assert set(BatchPredictResponse.model_fields)=={'predictions','total_rows'}
    assert ForecastRequest(Store=1,Dept=1).weeks==4
    with pytest.raises(ValidationError):ForecastRequest(Store=1,Dept=1,weeks=53)
    with pytest.raises(ValidationError):ForecastItem(Store=1,Dept=1,predicted_units=1.,model_version='production')
    assert set(ForecastResponse.model_fields)=={'forecasts'}


@pytest.mark.parametrize('module,classname',[
    ('src/services/forecast_service.py','ForecastService'),
    ('src/services/recursive_forecast_service.py','RecursiveForecastService')])
def test_characterize_original_constructors_still_require_predictor(module,classname):
    # Execute the real class definition only, with a trap at its first constructor
    # edge. Original operational modules/providers are never imported.
    tree=ast.parse((ROOT/module).read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==classname)
    predictor=Mock(side_effect=RuntimeError('stopped before native constructor'))
    scope={'Predictor':predictor}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),module,'exec'),scope)
    with pytest.raises(RuntimeError,match='stopped before native'):scope[classname]()
    predictor.assert_called_once_with(model_type='production')


@pytest.mark.parametrize('module',['api.main','src.celery_app','src.tasks','src.jobs.scheduler',
    'src.jobs.job_registry','src.jobs.forecast_job','src.jobs.forecast_accuracy_job',
    'src.jobs.report_generation_job','src.jobs.production_model_sync_job'])
def test_imports_have_no_operational_or_filesystem_effects(module,tmp_path):
    code=r'''
import builtins,io,logging,os,pathlib,signal,socket,sys
module=sys.argv[1]
original=builtins.__import__
blocked=('src.services','src.database','src.repositories','src.inference','src.ml',
 'src.preprocessing','src.deployment','src.core.logger','src.pdf_report',
 'sqlalchemy','psycopg2','redis','xgboost','lightgbm','torch','tensorflow','mlflow','apscheduler')
def importing(name,*args,**kwargs):
 if any(name==v or name.startswith(v+'.') for v in blocked):raise AssertionError('operational import: '+name)
 return original(name,*args,**kwargs)
def denied(*a,**kw):raise AssertionError('import attempted operational effect')
real_open=builtins.open;real_io_open=io.open;real_os_open=os.open
def reading(file,mode='r',*args,**kwargs):
 if any(v in mode for v in 'wax+'):denied()
 return real_open(file,mode,*args,**kwargs)
def io_reading(file,mode='r',*args,**kwargs):
 if any(v in mode for v in 'wax+'):denied()
 return real_io_open(file,mode,*args,**kwargs)
def os_reading(file,flags,*args,**kwargs):
 if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):denied()
 return real_os_open(file,flags,*args,**kwargs)
builtins.__import__=importing
builtins.open=reading;io.open=io_reading;os.open=os_reading
os.mkdir=denied;os.makedirs=denied;pathlib.Path.mkdir=denied
logging.FileHandler.__init__=denied;socket.create_connection=denied;socket.socket.connect=denied
signal.signal=denied
import kombu
kombu.Connection.__init__=denied
__import__(module)
if module=='src.celery_app':
 from src.celery_app import celery_app
 celery_app.loader.import_default_modules()
 assert 'src.tasks.forecast_next_week_task' in celery_app.tasks
print('SAFE')
'''
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH']=str(ROOT)+os.pathsep+env.get('PYTHONPATH','')
    result=subprocess.run([sys.executable,'-c',code,module],cwd=tmp_path,env=env,
                          text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='SAFE' and list(tmp_path.iterdir())==[]


@pytest.fixture
def runtime_api():
    from src.runtime.bootstrap import Runtime
    import api.main as api
    api.configure_runtime(Runtime())
    yield api,Runtime
    api.configure_runtime(Runtime())


def test_api_routes_and_unconfigured_fail_closed(runtime_api):
    api,Runtime=runtime_api
    from fastapi.testclient import TestClient
    assert {(m,r.path) for r in api.app.routes if r.path not in ('/openapi.json','/docs','/docs/oauth2-redirect','/redoc') for m in r.methods}==ROUTES
    with TestClient(api.app) as client:
        assert client.get('/health').json()==dict(status='ok',model_loaded=False,model_version='production')
        for path,kwargs in [('/predict',{'json':PAYLOAD}),('/forecast/week',{'json':{'Store':1,'Dept':2}}),
                            ('/forecast/async',{'params':{'store':1,'dept':2}})]:
            response=client.post(path,**kwargs);assert response.status_code==503
            assert response.json()=={'detail':'Runtime dependency unavailable'}
        assert client.get('/task/test').status_code==503
        assert client.get('/dashboard/accuracy').status_code==503


def test_api_injected_forecast_and_batch_failure_stay_legacy(runtime_api):
    api,Runtime=runtime_api;forecast=Mock();forecast.forecast_single.return_value=12.345
    forecast.forecast_dataframe.return_value=[12.345]
    api.configure_runtime(Runtime({'forecast':lambda:forecast}))
    from fastapi.testclient import TestClient
    with TestClient(api.app,raise_server_exceptions=False) as client:
        result=client.post('/predict',json=PAYLOAD)
        assert result.status_code==200 and result.json()==dict(predicted_units=12.35,model_version='production',Store=1,Dept=2,Date='2020-01-01')
        assert client.post('/batch_predict',json={'rows':[PAYLOAD]}).status_code==500
    forecast.forecast_single.assert_called_once()
    forecast.forecast_dataframe.assert_not_called()


def test_api_injected_recursive_task_dispatch_and_status(runtime_api):
    api,Runtime=runtime_api;recursive=Mock();recursive.forecast.return_value=[4.]
    task=Mock();task.delay.return_value=SimpleNamespace(id='stub-task')
    backend=Mock();backend.AsyncResult.return_value=SimpleNamespace(status='SUCCESS',ready=lambda:True,result={'stub':True})
    api.configure_runtime(Runtime({'recursive_forecast':lambda:recursive,'forecast_task':lambda:task,'task_backend':lambda:backend}))
    from fastapi.testclient import TestClient
    with TestClient(api.app) as client:
        assert client.post('/forecast/week',json={'Store':1,'Dept':2}).status_code==200
        assert client.post('/forecast',json={'Store':1,'Dept':2,'weeks':1}).status_code==200
        assert client.post('/forecast/async',params={'store':1,'dept':2}).json()=={'task_id':'stub-task','status':'submitted'}
        assert client.get('/task/stub-task').json()['result']=={'stub':True}
    task.delay.assert_called_once_with(1,2);backend.AsyncResult.assert_called_once_with('stub-task')
    assert recursive.forecast.call_args_list[0].kwargs==dict(store=1,dept=2,weeks=1)


@pytest.mark.parametrize('path,slot,method,value',[
    ('/dashboard/accuracy','dashboard','get_accuracy_summary',{'avg_absolute_error':1.,'avg_percentage_error':2.,'total_forecasts':3}),
    ('/dashboard/worst-forecasts','dashboard','get_worst_forecasts',[]),
    ('/dashboard/insights','dashboard','get_forecast_insights',{'summary':'stub','insights':[],'stats':{}}),
    ('/dashboard/job-history','dashboard','get_job_history',[]),
    ('/monitoring/health','monitoring','get_health_status',dict(api='healthy',database='stub',scheduler='stub',forecast_job='stub',accuracy_job='stub',report_job='stub')),
    ('/dashboard/report','report','generate_report',{'report_path':'stub.pdf'}),
    ('/metrics','job_health','refresh',None),
])
def test_read_and_report_routes_use_only_injected_edges(runtime_api,path,slot,method,value):
    api,Runtime=runtime_api;service=Mock();getattr(service,method).return_value=value
    factory=Mock(return_value=service);api.configure_runtime(Runtime({slot:factory}))
    from fastapi.testclient import TestClient
    with TestClient(api.app) as client:
        factory.assert_not_called()
        response=client.post(path) if path=='/dashboard/report' else client.get(path)
        assert response.status_code==200
        getattr(service,method).assert_called_once()
    factory.assert_called_once_with()


def test_constructor_failure_is_sanitized_and_not_success(runtime_api,caplog):
    api,Runtime=runtime_api
    def failing():raise RuntimeError('PRIVATE_BOOTSTRAP_SECRET')
    api.configure_runtime(Runtime({'forecast':failing}))
    from fastapi.testclient import TestClient
    with TestClient(api.app) as client:response=client.post('/predict',json=PAYLOAD)
    assert response.status_code==503 and 'PRIVATE_BOOTSTRAP_SECRET' not in response.text+caplog.text
    from src.runtime.bootstrap import RuntimeUnavailable
    with pytest.raises(RuntimeUnavailable) as caught:api.app.state.runtime.get('forecast')
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_celery_names_local_body_and_unconfigured_failure():
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    import src.tasks as tasks
    assert tasks.test_task.name=='src.tasks.test_task'
    assert tasks.forecast_next_week_task.name=='src.tasks.forecast_next_week_task'
    tasks.configure_runtime(Runtime())
    with pytest.raises(RuntimeUnavailable):tasks.forecast_next_week_task.run(1,2)
    factory=Mock();service=Mock();factory.return_value=service;service.forecast.return_value=[-1.]
    tasks.configure_runtime(Runtime({'recursive_forecast':factory}))
    try:
        assert factory.call_count==0
        assert tasks.forecast_next_week_task.run(1,2)==dict(Store=1,Dept=2,predicted_units=0.,model_version='production')
        service.forecast.assert_called_once_with(store=1,dept=2,weeks=1)
        assert tasks.test_task.run(2,3)==5
        with patch.object(tasks.forecast_next_week_task,'apply_async') as publish:
            tasks.forecast_next_week_task.delay(1,2);publish.assert_called_once_with((1,2),{})
    finally:tasks.configure_runtime(Runtime())


def test_registry_names_dispatch_and_laziness():
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    from src.jobs.job_registry import build_job_registry,JOB_REGISTRY
    assert {k:v['minutes'] for k,v in JOB_REGISTRY.items()}==JOBS
    factory=Mock();job=Mock();job.run.return_value='stub';factory.return_value=job
    registry=build_job_registry(Runtime({'forecast_job':factory}))
    assert factory.call_count==0 and callable(registry['forecast_job']['job'].run)
    assert registry['forecast_job']['job'].run()=='stub';job.run.assert_called_once_with()
    with pytest.raises(RuntimeUnavailable):registry['production_model_sync_job']['job'].run()


def metrics():
    counter=Mock();duration=SimpleNamespace(labels=lambda **kw:SimpleNamespace(time=lambda:nullcontext()))
    return SimpleNamespace(FORECAST_JOB_COUNTER=counter,ACCURACY_EVALUATIONS=counter,ACCURACY_JOB_COUNTER=counter,
                           JOB_DURATION=duration,REPORT_GENERATED=counter)


@pytest.mark.parametrize('name',['forecast','accuracy','report','sync'])
def test_real_job_orchestration_with_injected_edges(name):
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    from src.jobs.forecast_job import ForecastJob
    from src.jobs.forecast_accuracy_job import ForecastAccuracyJob
    from src.jobs.report_generation_job import ReportGenerationJob
    from src.jobs.production_model_sync_job import ProductionModelSyncJob
    cls={'forecast':ForecastJob,'accuracy':ForecastAccuracyJob,'report':ReportGenerationJob,'sync':ProductionModelSyncJob}[name]
    with pytest.raises(RuntimeUnavailable):cls().run()
    service=Mock();service.forecast.return_value=[1.]*4;service.evaluate_all_pending_forecasts.return_value=[{'stub':True}]
    service.generate_text_report.return_value='stub report';service.sync.return_value={'stub':True}
    execution=Mock();status=Mock();tracker=Mock();writer=Mock(return_value='stub.pdf');factory=Mock(return_value=service)
    runtime=Runtime({'recursive_forecast':factory,'accuracy_service':factory,'report_generator':factory,
        'production_sync':factory,'job_execution':lambda:execution,'job_status':lambda:status,
        'job_tracker':lambda:tracker,'job_metrics':metrics,'pdf_writer':lambda:writer})
    job=cls(runtime=runtime);assert factory.call_count==0;result=job.run()
    if name=='forecast':assert service.forecast.call_count==2 and all(c.kwargs['weeks']==4 for c in service.forecast.call_args_list)
    elif name=='accuracy':assert result==[{'stub':True}]
    elif name=='report':assert result=='stub.pdf';writer.assert_called_once_with('stub report')
    else:assert result=={'stub':True};service.sync.assert_called_once_with()


@pytest.mark.parametrize('name',['forecast','accuracy','report','sync'])
def test_job_failure_reaches_only_injected_failure_tracking(name):
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    from src.jobs.forecast_job import ForecastJob
    from src.jobs.forecast_accuracy_job import ForecastAccuracyJob
    from src.jobs.report_generation_job import ReportGenerationJob
    from src.jobs.production_model_sync_job import ProductionModelSyncJob
    cls={'forecast':ForecastJob,'accuracy':ForecastAccuracyJob,'report':ReportGenerationJob,'sync':ProductionModelSyncJob}[name]
    service=Mock();execution=Mock();tracker=Mock();status=Mock()
    for method in ('forecast','evaluate_all_pending_forecasts','generate_text_report','sync'):
        getattr(service,method).side_effect=RuntimeError('bounded stub failure')
    runtime=Runtime({'recursive_forecast':lambda:service,'accuracy_service':lambda:service,
        'report_generator':lambda:service,'production_sync':lambda:service,
        'job_execution':lambda:execution,'job_tracker':lambda:tracker,
        'job_status':lambda:status,'job_metrics':metrics,'pdf_writer':lambda:Mock()})
    with pytest.raises(RuntimeError,match='bounded stub failure'):cls(runtime=runtime).run()
    if name!='sync':execution.finish_failed.assert_called_once();execution.finish_success.assert_not_called()
    if name=='forecast':status.update_status.assert_called_once_with('forecast_job','failed')
    elif name!='sync':tracker.mark_failed.assert_called_once()
    # Partial bootstrap fails before job operations or tracking, even when the
    # first factory returned an object successfully.
    service.reset_mock();execution.reset_mock()
    partial=Runtime({'recursive_forecast':lambda:service,'accuracy_service':lambda:service,
                     'report_generator':lambda:service})
    with pytest.raises(RuntimeUnavailable):cls(runtime=partial).run()
    assert not service.mock_calls and not execution.mock_calls


def test_scheduler_registration_does_not_construct_jobs():
    from src.runtime.bootstrap import Runtime
    from src.jobs import scheduler
    fake=Mock();factories={k:Mock(side_effect=AssertionError('no construction')) for k in JOBS}
    runtime=Runtime(dict(factories,scheduler=lambda:fake))
    scheduler.configure_runtime(runtime)
    try:
        scheduler.register_jobs()
        assert fake.add_job.call_count==4
        assert {c.kwargs['id']:c.kwargs['minutes'] for c in fake.add_job.call_args_list}==JOBS
        for c in fake.add_job.call_args_list:assert callable(c.kwargs['func']) and c.kwargs['replace_existing'] is True
        assert not any(f.called for f in factories.values())
    finally:scheduler.configure_runtime(Runtime())


def test_actual_apscheduler_retains_ids_and_deferred_dispatch():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from src.jobs.job_registry import build_job_registry
    from src.jobs.scheduler import register_jobs
    from src.runtime.bootstrap import Runtime
    jobs={name:Mock() for name in JOBS}
    factories={name:Mock(return_value=job) for name,job in jobs.items()}
    registry=build_job_registry(Runtime(factories));scheduler=BlockingScheduler(timezone='UTC')
    register_jobs(scheduler_instance=scheduler,registry=registry)
    assert {job.id for job in scheduler.get_jobs()}==set(JOBS)
    assert not any(factory.called for factory in factories.values())
    assert scheduler.running is False
    for job in scheduler.get_jobs():job.func()
    assert all(job.run.call_count==1 for job in jobs.values())


@pytest.mark.parametrize('mode',['success','denied','failure'])
def test_scheduler_start_lock_and_failure_cleanup(mode):
    from src.runtime.bootstrap import Runtime
    from src.jobs import scheduler
    fake=Mock();lock=Mock();lock.acquire.return_value=mode!='denied'
    if mode=='failure':fake.add_job.side_effect=RuntimeError('stub registration failure')
    scheduler.configure_runtime(Runtime({'scheduler':lambda:fake,'scheduler_lock':lambda:lock}))
    try:
        if mode=='failure':
            with pytest.raises(RuntimeError):scheduler.start_scheduler(install_signal_handlers=False)
            lock.release.assert_called_once();fake.start.assert_not_called()
        else:
            scheduler.start_scheduler(install_signal_handlers=False)
            if mode=='success':
                fake.start.assert_called_once()
                with pytest.raises(SystemExit) as e:scheduler.shutdown_scheduler(None,None)
                assert e.value.code==0;lock.release.assert_called_once()
            else:fake.start.assert_not_called();fake.add_job.assert_not_called()
    finally:scheduler.configure_runtime(Runtime())


def test_scheduler_reconfiguration_is_denied_while_lock_is_held():
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    from src.jobs import scheduler
    active=Mock();lock=Mock();lock.acquire.return_value=True
    runtime=Runtime({'scheduler':lambda:active,'scheduler_lock':lambda:lock})
    scheduler.configure_runtime(runtime)
    def running():
        with pytest.raises(RuntimeUnavailable):scheduler.configure_runtime(Runtime())
        with pytest.raises(RuntimeUnavailable):scheduler.start_scheduler(install_signal_handlers=False)
    active.start.side_effect=running
    try:
        scheduler.start_scheduler(install_signal_handlers=False)
        lock.acquire.assert_called_once();lock.release.assert_called_once()
    finally:scheduler.configure_runtime(Runtime())


def test_runtime_factory_cache_reentrancy_and_concurrency():
    from src.runtime.bootstrap import Runtime,RuntimeUnavailable
    from concurrent.futures import ThreadPoolExecutor
    value=object();factory=Mock(return_value=value);runtime=Runtime({'forecast':factory})
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:runtime.get('forecast'),range(2)))==[value,value]
    factory.assert_called_once()
    recursive=None
    recursive=Runtime({'forecast':lambda:recursive.get('forecast')})
    with pytest.raises(RuntimeUnavailable):recursive.get('forecast')
    with pytest.raises(RuntimeUnavailable):Runtime({'unknown':factory})
    with pytest.raises(RuntimeUnavailable):Runtime({'forecast':lambda:None}).get('forecast')
    assert not recursive.is_resolved('forecast')
