"""Real SELECT-only forecast reads + explicitly synthetic labels. No model path."""
from datetime import timedelta
from pathlib import Path
import runpy
import json
import struct
from types import SimpleNamespace
from uuid import uuid4
import pytest
from conftest import make_batch, repository, target, admin, driver
from src.database import forecast_read_connection as connections
from src.services.canonical_forecast_read_service import build_canonical_forecast_reader
from src.services.stored_forecast_accuracy_service import build_stored_forecast_accuracy, LabelReadGrant
from src.data.contracts.canonical_forecast_read import ExactForecastRead
from src.data.contracts.stored_forecast_accuracy import ExactLabelRead

F=runpy.run_path(str(Path(__file__).parents[1]/'runtime/test_stored_forecast_accuracy.py'))


def setup(*,values=('500','650','500','650'),revision='first',change=None):
    batch=make_batch(values=[498.6732482910156,649.3267211914062]*2)
    committed=repository(batch)._append_initial(batch)  # TEST SETUP, not evaluator authority.
    op=object();clock=F['ClockControl'](op)
    fa,reader=build_canonical_forecast_reader(target('reader'),batch.scope,operator=op,clock_domain=clock.domain,
        wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'])
    fp=fa.establish_principal(op,subject_reference='synthetic:forecast-only',scope=batch.scope)
    fs=ExactForecastRead(scope=batch.scope,idempotency_id=batch.idempotency_id,expected_content_sha256=batch.content_sha256)
    fg=fa.authorize(op,fp,fs)
    snapshot=F['snapshot'](batch,values=values,revision=revision,change=change)
    la,service=build_stored_forecast_accuracy(reader,batch.scope,(snapshot,),operator=op,
        wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'])
    lp=la.establish_principal(op,subject_reference='synthetic:labels-only',scope=batch.scope)
    ls=ExactLabelRead(scope=batch.scope,revision_id=snapshot.revision_id,expected_snapshot_sha256=snapshot.content_sha256,
        interval_start=snapshot.interval_start,interval_end=snapshot.interval_end,evaluation_as_of=snapshot.interval_end+timedelta(days=1))
    lg=la.authorize(op,lp,ls)
    return SimpleNamespace(**locals())


def evaluate(s,**kw):return s.service.evaluate(kw.pop('fg',s.fg),kw.pop('fs',s.fs),kw.pop('lg',s.lg),kw.pop('ls',s.ls),**kw)


@pytest.fixture
def tracked(monkeypatch):
    real=driver();opened=[];statements=[];readonly=[]
    class Cursor(real.extensions.cursor):
        def execute(self,query,*a,**kw):
            statements.append(query)
            return super().execute(query,*a,**kw)
    class Connection(real.extensions.connection):
        def cursor(self,*a,**kw):
            kw['cursor_factory']=Cursor
            return super().cursor(*a,**kw)
        def set_session(self,*a,**kw):
            readonly.append(kw)
            return super().set_session(*a,**kw)
    def connect(**kw):
        assert kw['user']=='r7_forecast_reader'
        c=real.connect(**kw,connection_factory=Connection);opened.append(c);return c
    monkeypatch.setattr(connections,'driver',lambda:SimpleNamespace(connect=connect))
    return SimpleNamespace(opened=opened,statements=statements,readonly=readonly)


def test_genuine_readonly_evaluation_exact_bytes_and_no_writes(tracked):
    s=setup();before=repository(s.batch)._lookup(s.batch.idempotency_id)
    out=evaluate(s);e=out.evidence
    assert out.status=='VERIFIED_EVALUATION' and e.population_size==4
    assert e.forecast_batch_json==s.batch.canonical_json
    assert [p.prediction_float32_le for p in e.pairs]==['2d56f943','e9542244']*2
    assert len(tracked.opened)==1 and all(c.closed for c in tracked.opened)
    assert tracked.readonly==[dict(readonly=True,isolation_level='REPEATABLE READ',autocommit=False)]
    assert all(q.split()[0] in ('SELECT','SHOW') for q in tracked.statements)
    after=repository(s.batch)._lookup(s.batch.idempotency_id);assert before==after
    with admin() as c,c.cursor() as q:
        q.execute("SELECT encode(float32_le,'hex') FROM canonical_forecast.evidence WHERE organization_id=%s ORDER BY ordinal",(str(s.batch.scope.organization_id),))
        bits=[r[0] for r in q.fetchall()];assert bits==['2d56f943','e9542244']*2
        assert [struct.unpack('<f',bytes.fromhex(v))[0] for v in bits]==[498.6732482910156,649.3267211914062]*2


@pytest.mark.parametrize('fault',['tenant','dataset','series','revision','digest','interval','asof','foreign','revoked','principal_revoked','expired','forecast_revoked'])
def test_denial_before_real_connection(tracked,fault):
    s=setup();ls=s.ls;lg=s.lg
    if fault in ('tenant','dataset','series'):
        scope=ls.scope.model_dump();field={'tenant':'organization_id','dataset':'dataset_id','series':'series_ids'}[fault]
        scope[field]=(scope[field][0],) if fault=='series' else uuid4()
        ls=ls.model_copy(update={'scope':type(ls.scope)(**scope)})
    if fault in ('revision','digest'):ls=ls.model_copy(update={('revision_id' if fault=='revision' else 'expected_snapshot_sha256'):'f'*64})
    if fault in ('interval','asof'):
        field='interval_start' if fault=='interval' else 'evaluation_as_of';ls=ls.model_copy(update={field:getattr(ls,field)+timedelta(seconds=1)})
    if fault=='foreign':lg=LabelReadGrant()
    if fault=='revoked':s.la.revoke(s.op,lg)
    if fault=='principal_revoked':s.la.revoke(s.op,s.lp)
    if fault=='forecast_revoked':s.fa.revoke(s.op,s.fg)
    if fault=='expired':s.clock['elapsed']=30.
    assert evaluate(s,ls=ls,lg=lg).status=='DENIED'
    assert tracked.opened==tracked.statements==[]


@pytest.mark.parametrize('actuals',[('0',),('-1',),('500','650'),('501','651')])
def test_genuine_stored_batch_different_pinned_revisions(tracked,actuals):
    s=setup(values=actuals,revision=str(actuals));out=evaluate(s)
    assert out.status=='VERIFIED_EVALUATION'
    assert out.evidence.label_selection.revision_id==s.snapshot.revision_id
    assert out.evidence.label_snapshot_sha256==s.snapshot.content_sha256
    assert len(tracked.opened)==1 and tracked.opened[0].closed


def test_same_forecast_two_revisions_change_metrics_without_reforecast(tracked):
    s=setup();first=evaluate(s)
    snap=F['snapshot'](s.batch,values=('501','651'),revision='revised')
    # Explicit trusted HOST synthetic-source setup; never inferred from legacy rows.
    s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    selection=s.ls.model_copy(update={'revision_id':snap.revision_id,'expected_snapshot_sha256':snap.content_sha256})
    grant=s.la.authorize(s.op,s.lp,selection);second=evaluate(s,ls=selection,lg=grant)
    assert first.evidence.forecast_batch_json==second.evidence.forecast_batch_json
    assert first.evidence.mae!=second.evidence.mae
    assert first.evidence.content_sha256!=second.evidence.content_sha256
    assert len(tracked.opened)==2 and all(c.closed for c in tracked.opened)


@pytest.mark.parametrize('kind',['late','missing','units','calendar'])
def test_unready_and_semantic_mismatch_with_real_verified_forecasts(tracked,kind):
    s=setup();snap=s.snapshot
    if kind=='missing':snap=snap.model_copy(update={'labels':snap.labels[:-1]})
    if kind=='late':snap=snap.model_copy(update={'labels':tuple(r.model_copy(update={'available_at':s.ls.evaluation_as_of+timedelta(seconds=1)}) for r in snap.labels)})
    if kind in ('units','calendar'):
        field='measurement_json' if kind=='units' else 'temporal_json'
        snap=snap.model_copy(update={'labels':tuple(r.model_copy(update={'target':r.target.model_copy(update={field:F['operational_json'](dict(json.loads(getattr(r.target,field)), **({'unit':'other-unit'} if kind=='units' else {'timezone':'America/New_York'})))})}) for r in snap.labels)})
    snap=type(snap).model_validate_json(snap.canonical_json);s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    selection=s.ls.model_copy(update={'expected_snapshot_sha256':snap.content_sha256});grant=s.la.authorize(s.op,s.lp,selection)
    assert evaluate(s,ls=selection,lg=grant).status==('LABELS_NOT_READY' if kind in ('late','missing') else 'INCOMPATIBLE_SCOPE')
    assert len(tracked.opened)==1 and tracked.opened[0].closed


def test_alternating_tenants_fresh_scopes(tracked):
    a=setup();b=setup()
    for s in (a,b,a,b):
        out=evaluate(s);assert out.status=='VERIFIED_EVALUATION'
        assert out.evidence.label_selection.scope==s.batch.scope
    assert len(tracked.opened)==4 and all(c.closed for c in tracked.opened)


@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit])
def test_read_interruption_closes_connection_no_evaluation(tracked,monkeypatch,error):
    from src.repositories.forecast_read_repository import ForecastReadRepository
    s=setup()
    def fail(*_):raise error('injected')
    monkeypatch.setattr(ForecastReadRepository,'_find',fail)
    with pytest.raises(error):evaluate(s)
    assert len(tracked.opened)==1 and tracked.opened[0].closed


def test_corrupt_stored_evidence_never_scored(tracked):
    s=setup()
    with admin() as c,c.cursor() as q:
        q.execute('SET LOCAL session_replication_role=replica')
        q.execute('DELETE FROM canonical_forecast.current_records WHERE organization_id=%s',(str(s.batch.scope.organization_id),))
        c.commit()
    assert evaluate(s).status=='UNAVAILABLE'
    assert len(tracked.opened)==1 and tracked.opened[0].closed


def test_weekly_actual_aggregation_cannot_match_daily_forecast(tracked):
    s=setup();snap=s.snapshot
    def weekly(row):
        temporal=json.loads(row.target.temporal_json);temporal.update(frequency='weekly',week_start=0)
        return row.model_copy(update={'target':row.target.model_copy(update={'temporal_json':F['operational_json'](temporal)})})
    snap=type(snap).model_validate_json(snap.model_copy(update={'labels':tuple(weekly(r) for r in snap.labels)}).canonical_json)
    s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    selection=s.ls.model_copy(update={'expected_snapshot_sha256':snap.content_sha256})
    grant=s.la.authorize(s.op,s.lp,selection)
    assert evaluate(s,ls=selection,lg=grant).status=='INCOMPATIBLE_SCOPE'
    assert len(tracked.opened)==1 and tracked.opened[0].closed
