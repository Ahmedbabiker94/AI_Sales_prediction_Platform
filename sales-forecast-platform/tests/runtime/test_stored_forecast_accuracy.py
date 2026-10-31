"""Contract arithmetic/authority tests. Mocked forecast storage is NOT PostgreSQL proof."""
import ast
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import pickle
import struct
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
import pytest
from src.data.contracts.dataset import operational_json
from src.data.contracts.features import TemporalFeatureRules
from src.data.contracts.mapping import _DeclaredMeasurement
from src.data.contracts.forecast_evidence_storage import (
    ForecastStorageScope, ForecastLogicalKey, ForecastStorageLineage, ForecastEvidenceRow,
    CanonicalForecastStorageBatch, StoredForecastBatch, ForecastStorageReceipt,
)
from src.data.contracts.canonical_forecast_read import ExactForecastRead
from src.data.contracts.stored_forecast_accuracy import (
    ActualLabel, LabelTarget, SyntheticLabelSnapshot, ExactLabelRead, AccuracyOutcome,
)
from src.database.offline_forecast_postgres import OfflineForecastPostgres
from src.providers.forecast_read_provider import ForecastReadProvider
from src.services.canonical_forecast_read_service import build_canonical_forecast_reader, ExactReadGrant, HostReadClock
from src.services.stored_forecast_accuracy_service import (
    build_stored_forecast_accuracy, LabelPrincipal, LabelReadGrant, LabelReadDenied,
)


def digest(v): return hashlib.sha256(v.encode()).hexdigest()


def make_batch():
    scope=ForecastStorageScope(organization_id=uuid4(),dataset_id=uuid4(),series_ids=tuple(sorted(uuid4() for _ in range(4))))
    origin=datetime(2026,1,5,tzinfo=timezone.utc)
    measurement=_DeclaredMeasurement(metric='sales',unit='count',currency=None)
    temporal=TemporalFeatureRules(frequency='daily',calendar='gregorian',timezone='UTC',period_anchor='start',
        week_start=None,lag_basis='period',gap_policy='reject',minimum_history=2,cold_start='reject',recursive_strategy='provided_predictions')
    lineage=ForecastStorageLineage(**{field:digest(field) for field in
        ('dataset_version','publication_id','capture_reference','artifact_id','artifact_sha256','executable_input_id',
         'snapshot_id','representation_id','frozen_state_id','owner_reference','execution_id','producer_issuance_id')},capture_cutoff=origin)
    rows=[]
    for i,series in enumerate(reversed(scope.series_ids)):
        key=ForecastLogicalKey(organization_id=scope.organization_id,dataset_id=scope.dataset_id,series_id=series,
            candidate_id=digest('candidate'),family='xgboost',forecast_origin=origin,target_period=origin,
            target_period_end=origin+timedelta(days=1),target_metric='sales',
            measurement_json=operational_json(measurement.model_dump(mode='json')),temporal_json=operational_json(temporal.model_dump(mode='json')))
        rows.append(ForecastEvidenceRow(key=key,lineage=lineage,float32_le=['2d56f943','e9542244'][i%2]))
    return CanonicalForecastStorageBatch(scope=scope,idempotency_id=digest(str(uuid4())),rows=tuple(rows))


def stored(batch):
    receipt=ForecastStorageReceipt(receipt_id=uuid4(),scope=batch.scope,idempotency_id=batch.idempotency_id,
        content_sha256=batch.content_sha256,transaction_timestamp=batch.rows[0].key.target_period_end,
        row_sha256=tuple(r.row_sha256 for r in batch.rows))
    return StoredForecastBatch(batch=batch,receipt=receipt)


def snapshot(batch,*,values=('500','650','500','650'),revision='original',change=None):
    end=batch.rows[0].key.target_period_end
    labels=tuple(ActualLabel(target=LabelTarget.from_forecast(r.key),decimal_value=values[i%len(values)],
        available_at=end+timedelta(hours=2)) for i,r in enumerate(batch.rows))
    s=SyntheticLabelSnapshot(scope=batch.scope,revision_id=digest(revision),published_at=end+timedelta(hours=1),
        effective_at=end,interval_start=batch.rows[0].key.target_period,interval_end=end,labels=labels)
    return s if change is None else SyntheticLabelSnapshot.model_validate_json(s.model_copy(update=change).canonical_json)


class ClockControl(dict):
    """Synthetic HOST test control, never a production clock or consumer API."""
    def __init__(self, operator, values=None):
        super().__init__({'wall':100., 'elapsed':0.} if values is None else values)
        self.operator = operator
        self.domain = HostReadClock(operator, synthetic_start=(self['wall'], self['elapsed']))

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.domain.set_synthetic(self.operator, wall=self['wall'], elapsed=self['elapsed'])

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value


def setup_context(batch, snaps, *, reader=None, clock=None):
    op=object()
    clock=ClockControl(op, clock)
    if reader is None:
        instance=UUID(int=1)
        target=OfflineForecastPostgres('r7_05b3_'+instance.hex,instance,'reader','test-only-password',True)
        fa,reader=build_canonical_forecast_reader(target,batch.scope,operator=op,clock_domain=clock.domain,
            wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'])
        fp=fa.establish_principal(op,subject_reference='synthetic:forecast-reader',scope=batch.scope)
        fs=ExactForecastRead(scope=batch.scope,idempotency_id=batch.idempotency_id,expected_content_sha256=batch.content_sha256)
        fg=fa.authorize(op,fp,fs)
    la,service=build_stored_forecast_accuracy(reader,batch.scope,snaps,operator=op,
        wall_clock=lambda:clock['wall'],elapsed_clock=lambda:clock['elapsed'])
    lp=la.establish_principal(op,subject_reference='synthetic:independent-label-reader',scope=batch.scope)
    snap=snaps[0]
    ls=ExactLabelRead(scope=batch.scope,revision_id=snap.revision_id,expected_snapshot_sha256=snap.content_sha256,
        interval_start=snap.interval_start,interval_end=snap.interval_end,evaluation_as_of=snap.interval_end+timedelta(days=1))
    lg=la.authorize(op,lp,ls)
    return SimpleNamespace(**locals())


@pytest.fixture
def context(monkeypatch):
    b=make_batch();s=setup_context(b,(snapshot(b),))
    s.lookup=Mock(return_value=stored(b));monkeypatch.setattr(ForecastReadProvider,'lookup',s.lookup)
    return s


def evaluate(s,**kwargs):
    return s.service.evaluate(kwargs.pop('fg',s.fg),kwargs.pop('fs',s.fs),kwargs.pop('lg',s.lg),kwargs.pop('ls',s.ls),**kwargs)


def replace_request(s,**kwargs):return ExactLabelRead.model_validate_json(s.ls.model_copy(update=kwargs).canonical_json)


def test_exact_metrics_and_forecast_bits_immutable(context):
    s=context;before=s.batch.canonical_bytes;out=evaluate(s)
    assert out.status=='VERIFIED_EVALUATION'
    e=out.evidence;pred=[498.6732482910156,649.3267211914062]*2
    errors=[p-a for p,a in zip(pred,[500,650]*2)]
    assert e.mae==math.fsum(abs(v) for v in errors)/4
    assert e.bias==math.fsum(errors)/4
    assert e.rmse==math.sqrt(math.fsum(v*v for v in errors)/4)
    assert [p.prediction_float32_le for p in e.pairs]==['2d56f943','e9542244']*2
    assert [p.ordinal for p in e.pairs]==list(range(4))
    assert s.batch.canonical_bytes==before and e.forecast_batch_json==s.batch.canonical_json
    assert len(e.content_sha256)==64 and e==evaluate(s).evidence
    with pytest.raises(ValueError):e.bias=0.
    assert e.population_size==e.matched_count==4 and e.missing_count==e.ineligible_count==0


@pytest.mark.parametrize('handle',[None,{},'a'*64,object(),LabelReadGrant(),LabelPrincipal(),ExactReadGrant()])
def test_unoriginal_grant_denied_before_forecast_port(context,handle):
    assert evaluate(context,lg=handle).canonical_json==AccuracyOutcome(status='DENIED').canonical_json
    context.lookup.assert_not_called()


@pytest.mark.parametrize('field',['scope_tenant','scope_dataset','scope_series','revision_id','expected_snapshot_sha256','interval_start','interval_end','evaluation_as_of'])
def test_label_request_substitution_zero_port(context,field):
    s=context
    if field.startswith('scope_'):
        d=s.ls.scope.model_dump();k={'scope_tenant':'organization_id','scope_dataset':'dataset_id','scope_series':'series_ids'}[field]
        d[k]=(d[k][0],) if k=='series_ids' else uuid4();request=replace_request(s,scope=ForecastStorageScope(**d))
    elif field in ('revision_id','expected_snapshot_sha256'):request=replace_request(s,**{field:'f'*64})
    else:request=replace_request(s,**{field:getattr(s.ls,field)+timedelta(seconds=1)})
    assert evaluate(s,ls=request).status=='DENIED';s.lookup.assert_not_called()


@pytest.mark.parametrize('field',['organization_id','dataset_id','series_ids','idempotency_id','expected_content_sha256'])
def test_forecast_request_substitution_zero_port(context,field):
    s=context;request=s.fs
    if field in ('organization_id','dataset_id','series_ids'):
        d=request.scope.model_dump();d[field]=(d[field][0],) if field=='series_ids' else uuid4()
        request=request.model_copy(update={'scope':ForecastStorageScope(**d)})
    else:request=request.model_copy(update={field:'f'*64})
    assert evaluate(s,fs=request).status=='DENIED';s.lookup.assert_not_called()


@pytest.mark.parametrize('which',['lp','lg'])
@pytest.mark.parametrize('operation',[copy.copy,copy.deepcopy,pickle.dumps])
def test_handles_not_reconstructed(context,which,operation):
    with pytest.raises(TypeError):operation(getattr(context,which))


@pytest.mark.parametrize('fault',['grant_revoked','principal_revoked','wall','elapsed','backwards','nan','cancel','raise','foreign_forecast'])
def test_invalid_authority_or_callbacks_no_lookup(context,fault):
    s=context;kw={}
    if fault.endswith('revoked'):s.la.revoke(s.op,s.lp if fault=='principal_revoked' else s.lg)
    if fault=='wall':s.clock['wall']=130.
    if fault=='elapsed':s.clock['elapsed']=30.
    if fault=='backwards':s.clock['wall']=99.
    if fault=='nan':s.clock['wall']=float('nan')
    if fault=='foreign_forecast':kw['fg']=ExactReadGrant()
    def stop():
        if fault=='raise':raise RuntimeError('secret')
        return fault=='cancel'
    assert evaluate(s,cancelled=stop,**kw).status=='DENIED';s.lookup.assert_not_called()


@pytest.mark.parametrize('edge',['label_wall','label_elapsed','forecast_wall','forecast_elapsed','cancel'])
@pytest.mark.parametrize('which',['label','forecast'])
def test_callback_revocation_discards_completed_read(context,edge,which):
    s=context
    def revoke(value):
        if s.lookup.called:
            (s.la if which=='label' else s.fa).revoke(s.op,s.lg if which=='label' else s.fg)
        return value
    if edge=='label_wall':s.la._state.wall=lambda:revoke(100.)
    if edge=='label_elapsed':s.la._state.elapsed=lambda:revoke(0.)
    if edge=='forecast_wall':s.reader._state.wall=lambda:revoke(100.)
    if edge=='forecast_elapsed':s.reader._state.elapsed=lambda:revoke(0.)
    assert evaluate(s,cancelled=(lambda:revoke(False)) if edge=='cancel' else lambda:False).status=='DENIED'
    s.lookup.assert_called_once()


@pytest.mark.parametrize('field',['operator','principal','scope','lifetime'])
def test_host_only_independent_label_issuance(context,field):
    s=context
    with pytest.raises(LabelReadDenied):
        if field=='operator':s.la.authorize(object(),s.lp,s.ls)
        if field=='principal':s.la.authorize(s.op,s.fp,s.ls)
        if field=='scope':s.la.establish_principal(s.op,subject_reference='x',scope=s.batch.scope.model_copy(update={'organization_id':uuid4()}))
        if field=='lifetime':s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=61)


@pytest.mark.parametrize('fault',['missing','late','published','effective','before_end','not_registered','bad_digest'])
def test_labels_not_ready_vs_unavailable(context,fault):
    s=context;original=s.snap
    if fault=='missing':snap=snapshot(s.batch,change={'labels':original.labels[:-1]})
    elif fault=='late':snap=snapshot(s.batch,change={'labels':tuple(r.model_copy(update={'available_at':s.ls.evaluation_as_of+timedelta(seconds=1)}) for r in original.labels)})
    elif fault=='published':
        t=s.ls.evaluation_as_of+timedelta(seconds=1)
        snap=snapshot(s.batch,change={'published_at':t,'labels':tuple(r.model_copy(update={'available_at':t}) for r in original.labels)})
    elif fault=='effective':snap=snapshot(s.batch,change={'effective_at':s.ls.evaluation_as_of+timedelta(seconds=1)})
    else:snap=original
    # Explicit host setup for a different pinned snapshot, no synthetic latest fallback.
    s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    request=replace_request(s,expected_snapshot_sha256=snap.content_sha256)
    if fault=='before_end':request=request.model_copy(update={'evaluation_as_of':snap.interval_end-timedelta(seconds=1)})
    if fault=='not_registered':request=request.model_copy(update={'revision_id':'f'*64})
    if fault=='bad_digest':request=request.model_copy(update={'expected_snapshot_sha256':'f'*64})
    grant=s.la.authorize(s.op,s.lp,request)
    assert evaluate(s,lg=grant,ls=request).status==('UNAVAILABLE' if fault=='bad_digest' else 'LABELS_NOT_READY')


@pytest.mark.parametrize('field',['target_metric','measurement_json','temporal_json','target_interval'])
def test_authorized_but_incompatible_semantics(context,field):
    s=context;snap=s.snap
    if field=='target_interval':
        shift=timedelta(days=1)
        labels=tuple(r.model_copy(update={'target':r.target.model_copy(update={'target_period':r.target.target_period+shift,'target_period_end':r.target.target_period_end+shift})}) for r in snap.labels)
        snap=snapshot(s.batch,change={'interval_start':snap.interval_start+shift,'interval_end':snap.interval_end+shift,'labels':labels})
    else:
        value='revenue' if field=='target_metric' else operational_json(dict(json.loads(getattr(snap.labels[0].target,field)), **({'unit':'other-unit'} if field=='measurement_json' else {'timezone':'America/New_York'})))
        snap=snapshot(s.batch,change={'labels':tuple(r.model_copy(update={'target':r.target.model_copy(update={field:value})}) for r in snap.labels)})
    s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    request=replace_request(s,expected_snapshot_sha256=snap.content_sha256,interval_start=snap.interval_start,interval_end=snap.interval_end)
    grant=s.la.authorize(s.op,s.lp,request)
    assert evaluate(s,lg=grant,ls=request).status=='INCOMPATIBLE_SCOPE'


@pytest.mark.parametrize('values',[('0',),('-1',),('500','650'),('501','651')])
def test_revision_zero_negative_policy(context,values):
    s=context;snap=snapshot(s.batch,values=values,revision=str(values));s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    request=replace_request(s,revision_id=snap.revision_id,expected_snapshot_sha256=snap.content_sha256)
    grant=s.la.authorize(s.op,s.lp,request);out=evaluate(s,lg=grant,ls=request)
    errors=[p-float(values[i%len(values)]) for i,p in enumerate([498.6732482910156,649.3267211914062]*2)]
    assert out.evidence.mae==math.fsum(abs(e) for e in errors)/4
    assert out.evidence.bias==math.fsum(errors)/4
    assert out.evidence.label_selection.revision_id==snap.revision_id
    assert 'mape' not in type(out.evidence).model_fields


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity','bad','1e1000','1e-1000',' 1','1_0'])
def test_invalid_actual_values_rejected(context,value):
    with pytest.raises(ValueError):ActualLabel(target=context.snap.labels[0].target,decimal_value=value,available_at=context.snap.published_at)


def test_duplicate_labels_and_foreign_membership_rejected(context):
    s=context
    for labels in [(s.snap.labels[0],)*2,(s.snap.labels[0].model_copy(update={'target':s.snap.labels[0].target.model_copy(update={'series_id':uuid4()})}),)]:
        with pytest.raises(ValueError):snapshot(s.batch,change={'labels':labels})


@pytest.mark.parametrize('fault',['not_found','unavailable','detached','provider_exception'])
def test_no_unverified_forecast_inputs(context,fault):
    s=context
    if fault=='not_found':s.lookup.return_value=None
    if fault=='detached':s.lookup.return_value={'status':'VERIFIED_RECORD_SET'}
    if fault in ('provider_exception','unavailable'):s.lookup.side_effect=RuntimeError('private')
    assert evaluate(s).canonical_json==AccuracyOutcome(status='UNAVAILABLE').canonical_json


def test_large_finite_metrics_avoid_intermediate_square_overflow(context):
    s=context;snap=snapshot(s.batch,values=('1E+300',));s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    request=replace_request(s,expected_snapshot_sha256=snap.content_sha256);grant=s.la.authorize(s.op,s.lp,request)
    out=evaluate(s,lg=grant,ls=request)
    assert out.status=='VERIFIED_EVALUATION'
    assert out.evidence.rmse==out.evidence.mae==1e300 and out.evidence.bias==-1e300


def test_no_models_sql_or_operational_imports_and_no_writer_api():
    paths=['src/services/stored_forecast_accuracy_service.py','src/data/contracts/stored_forecast_accuracy.py']
    for p in paths:
        tree=ast.parse(Path(p).read_text())
        names=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(any(word in name for word in ('mlflow','xgboost','database.db','forecast_accuracy_service','forecast_write_intent')) for name in names)
        assert not any(isinstance(n,ast.Attribute) and n.attr in ('execute','connect','save_accuracy','predict','fit','_append_initial') for n in ast.walk(tree))


@pytest.mark.parametrize('error',[KeyboardInterrupt,SystemExit])
def test_interruption_propagates_without_result_or_writes(context,error):
    context.lookup.side_effect=error('injected')
    with pytest.raises(error):evaluate(context)
    context.lookup.assert_called_once()


def test_foreign_service_grants_not_interchangeable(context):
    s=context;other=setup_context(s.batch,(s.snap,))
    assert evaluate(s,lg=other.lg).status=='DENIED'
    assert evaluate(s,fg=other.fg).status=='DENIED'
    assert evaluate(s,lg=s.fg).status=='DENIED'
    assert evaluate(s,fg=s.lg).status=='DENIED'
    s.lookup.assert_not_called()


def test_independent_label_order_not_forecast_reorder(context):
    s=context;snap=snapshot(s.batch,change={'labels':tuple(reversed(s.snap.labels))})
    s.la._state.snapshots[snap.revision_id]=snap.canonical_json
    request=replace_request(s,expected_snapshot_sha256=snap.content_sha256)
    out=evaluate(s,ls=request,lg=s.la.authorize(s.op,s.lp,request))
    assert out.status=='VERIFIED_EVALUATION'
    assert [p.logical_key_id for p in out.evidence.pairs]==[r.key.logical_key_id for r in s.batch.rows]


def test_pinned_snapshot_copies_input_and_result_contains_original_lineage(context):
    s=context;object.__setattr__(s.snap,'revision_id','f'*64)
    out=evaluate(s)
    assert out.status=='VERIFIED_EVALUATION'
    batch=CanonicalForecastStorageBatch.model_validate_json(out.evidence.forecast_batch_json)
    assert batch.rows[0].lineage==s.batch.rows[0].lineage
    assert batch.rows[0].key==s.batch.rows[0].key


@pytest.mark.parametrize('fault',['reader','state','labels'])
def test_host_reference_substitution_denied(context,fault):
    s=context
    if fault=='reader':s.service._reader=object()
    if fault=='state':s.reader._state=object()
    if fault=='labels':s.service._labels=object()
    assert evaluate(s).status=='DENIED';s.lookup.assert_not_called()


@pytest.mark.parametrize('edge',['wall','elapsed'])
def test_label_expiry_independent_of_valid_forecast_grant(context,edge):
    s=context
    s.lg=s.la.authorize(s.op,s.lp,s.ls,lifetime_seconds=1)
    s.clock[edge]=101. if edge=='wall' else 1.
    assert evaluate(s).status=='DENIED';s.lookup.assert_not_called()


def test_exact_availability_asof_boundary_is_explicitly_eligible(context):
    s=context;request=replace_request(s,evaluation_as_of=s.snap.labels[0].available_at)
    grant=s.la.authorize(s.op,s.lp,request)
    assert evaluate(s,lg=grant,ls=request).status=='VERIFIED_EVALUATION'


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_family_independent_arithmetic_not_model_execution(context,family):
    batch=context.batch.model_copy(update={'rows':tuple(r.model_copy(update={'key':r.key.model_copy(update={'family':family})}) for r in context.batch.rows)})
    batch=type(batch).model_validate_json(batch.canonical_json)
    s=setup_context(batch,(snapshot(batch),));context.lookup.return_value=stored(batch)
    out=evaluate(s)
    assert out.status=='VERIFIED_EVALUATION'
    assert type(batch).model_validate_json(out.evidence.forecast_batch_json).rows[0].key.family==family


def test_tiny_actual_rmse_does_not_underflow_when_squaring(context):
    batch=context.batch.model_copy(update={'rows':tuple(r.model_copy(update={'float32_le':'00000000'}) for r in context.batch.rows)})
    batch=type(batch).model_validate_json(batch.canonical_json)
    s=setup_context(batch,(snapshot(batch,values=('1E-300',)),));context.lookup.return_value=stored(batch)
    out=evaluate(s)
    assert out.status=='VERIFIED_EVALUATION'
    assert out.evidence.mae==out.evidence.rmse==1e-300 and out.evidence.bias==-1e-300
