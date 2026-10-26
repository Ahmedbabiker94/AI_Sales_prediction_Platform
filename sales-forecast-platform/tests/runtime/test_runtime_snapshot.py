"""R7-02: real finalized repository/provider fixtures; no model operations."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import runpy
import struct
import traceback
from unittest.mock import Mock
import warnings

import pandas as pd
import pytest

from src.data.contracts.dataset import operational_json
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest, RuntimeInputSnapshot
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService, RuntimeSnapshotError
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, ValueAttestedAvailabilityVerifier, historical_value_identity,
)


@pytest.fixture
def source(tmp_path):
    factory = runpy.run_path('tests/training/test_temporal_training_preparation.py')['make_temporal'].__wrapped__(tmp_path)
    def make(*, exo=False, four=True, rule=None):
        def expand(raw):
            if four:
                other=raw.copy(deep=True);other['group']='food';other['reported']+=1000
                for row in other.to_dict('records'):raw.loc[len(raw)]=row
        b=factory(exo=exo,raw_change=expand)
        if rule is not None:
            cfg=b['configuration'].model_dump();cfg['execution']['exogenous'][0].update(availability=rule,offset=None)
            b['configuration']=FeatureConfiguration.model_validate(cfg)
            plan=b['plan'].model_dump();plan['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
            b['plan']=type(b['plan']).model_validate(plan)
        if rule=='external_forecast':b['service'].authorize_external=lambda *_:True
        result=b['service'].prepare_temporal(b['plan'],b['configuration'])
        base=result.origins[0].preparation
        metadata=b['metadata'][0];raw=b['raws'][metadata.reference.organization_id.int].copy(deep=True)
        ledger={}
        with b['provider'].open_read(metadata.reference,purpose='production_training') as session:
            for chunk in session.batches():
                for row in chunk.to_dict('records'):
                    expected=raw.iloc[int(row['source_record_id'])]
                    assert row['value']==expected.reported
                    ledger[row['source_record_id']]=(row['series_id'],row['lineage_id'],expected)
        events=[];control={'deny':None,'cancel':False,'clock':0.,'hook':None}
        def authorize(req,ctx,stage):
            events.append(stage)
            if control['hook']:control['hook'](stage)
            return stage!=control['deny']
        def availability(ctx,claims):
            assert ctx.metadata==metadata
            assert RuntimeSnapshotRequest.model_validate_json(ctx.request_json).capture_cutoff==ctx.capture_cutoff
            for claim in claims:
                sid,lineage,row=ledger[claim.source_record_id]
                release=row['released' if claim.field=='value' else 'promo_release']
                proof=row['proof' if claim.field=='value' else 'promo_proof']
                if ((claim.series_id,claim.lineage_id)!=(sid,lineage)
                        or claim.period_start!=row['from'] or claim.period_end!=row['to']
                        or claim.available_at!=release or claim.evidence_reference!=proof
                        or claim.available_at>ctx.capture_cutoff):return False
            return True
        def values(ctx,claims):
            for claim in claims:
                a=claim.availability;row=ledger[a.source_record_id][2]
                if (a.value_identity!=historical_value_identity(float(row.reported))
                        or claim.dataset_reference!=metadata.reference
                        or claim.manifest_digest!=metadata.manifest_digest
                        or claim.admission_digest!=metadata.admission_digest
                        or claim.organization_id!=metadata.reference.organization_id):return False
            return True
        args=dict(schema=base.schema,fitted_state=base.fitted_state,authorize_snapshot=authorize,
            verify_history=ValueAttestedAvailabilityVerifier(verify_availability=availability,verify_captured_values=values),
            elapsed_clock=lambda:control['clock'])
        service=CanonicalRuntimeSnapshotService(b['provider'],**args)
        request=RuntimeSnapshotRequest(dataset=b['plan'].datasets[0],purpose='production_training',
            forecast_origin=b['at'](6),capture_cutoff=b['at'](6),feature_schema_json=operational_json(base.schema.model_dump(mode='json')),
            history_periods=4,static_fields=('location','department'),external_fields=('promotion',) if exo else ())
        return dict(b=b,base=base,result=result,service=service,request=request,ledger=ledger,args=args,
                    events=events,control=control)
    return make


def forbidden(*a,**kw):raise AssertionError('no feature fitting/execution or native model operations')


def test_real_four_series_one_capture_deterministic_and_immutable(source,monkeypatch):
    s=source();b=s['b'];provider=b['provider'];reads=[];original=provider.open_read
    @contextmanager
    def counted(*args,**kwargs):
        reads.append(1)
        with original(*args,**kwargs) as session:
            yield session
            assert session.completed
    monkeypatch.setattr(provider,'open_read',counted)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',forbidden)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',forbidden,raising=False)
    first=s['service'].capture(s['request']);assert len(reads)==1
    assert len(first.history)==16 and len(first.static)==4 and first.captured_rows==48
    assert not first.external and not first.historical_covariates and first.classification=='DATA_READINESS_ONLY'
    for row in first.history:
        expected=s['ledger'][row.source_record_id][2]
        assert row.value==expected.reported and row.period_end<=first.request.forecast_origin
        assert row.available_at<=first.request.capture_cutoff
    assert len({tuple(x.dimensions) for x in first.static})==4
    restored=RuntimeInputSnapshot.model_validate_json(first.canonical_json)
    assert restored==first and restored.snapshot_id==first.snapshot_id
    second=s['service'].capture(s['request']);assert len(reads)==2
    assert second.canonical_bytes==first.canonical_bytes and second.snapshot_id==first.snapshot_id
    assert s['events']==['admit','read','after_read','return']*2
    with pytest.raises(Exception):first.history[0].value=999.
    with pytest.raises(Exception):first.static[0].dimensions+=(('x','y'),)
    with pytest.raises(Exception):first.request.dataset.series_ids=()


@pytest.mark.parametrize('field,value',[
    ('forecast_origin',None),('capture_cutoff',None),('forecast_horizon',2),('history_periods',3),
    ('static_fields',('Size',)),('external_fields',('Temperature',)),('purpose','runtime_inference'),
])
def test_unsupported_request_before_capture(source,monkeypatch,field,value):
    s=source();read=Mock(side_effect=forbidden);monkeypatch.setattr(s['b']['provider'],'open_read',read)
    malformed=s['request'].model_copy(update={field:value})
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(malformed)
    read.assert_not_called()


@pytest.mark.parametrize('fault',['tenant','series','revision','mapping','schema','feature_order','static'])
def test_scope_or_schema_substitution(source,monkeypatch,fault):
    from uuid import UUID
    s=source();r=s['request'];data=r.model_dump()
    if fault in ('tenant','series','revision','mapping'):
        binding=r.dataset.model_dump()
        if fault=='tenant':
            binding['reference']['organization_id']=UUID(int=999)
        elif fault=='series':binding['series_ids']=(UUID(int=999),)
        elif fault=='revision':binding['admission_digest']='0'*64
        else:binding['mapping_digest']='0'*64
        data['dataset']=binding
    elif fault=='static':data['static_fields']=('location','location')
    else:
        schema=json.loads(data['feature_schema_json'])
        if fault=='schema':schema['fitted_state_id']='0'*64
        else:schema['configuration']['ordered_outputs'].reverse()
        data['feature_schema_json']=json.dumps(schema,separators=(',',':'))
    with pytest.raises(Exception):
        request=RuntimeSnapshotRequest.model_validate(data)
        s['service'].capture(request)


@pytest.mark.parametrize('fault',['missing','duplicate','cross_series','future_event','late','revision','dtype'])
def test_capture_corruption_fails_closed(source,monkeypatch,fault):
    s=source();original=CanonicalTrainingPreparationService._capture
    def changed(*args,**kwargs):
        meta,ctx,frame,defs=original(*args,**kwargs)
        # This models a compromised/substituted detached capture; the provider's
        # real persisted integrity checks are ALSO exercised separately below.
        candidate=frame[(frame.period_start>=s['b']['at'](2))&(frame.period_end<=s['b']['at'](6))].index[0]
        if fault=='missing':frame=frame.drop(candidate)
        elif fault=='duplicate':frame=pd.concat([frame,frame.loc[[candidate]]],ignore_index=True)
        elif fault=='cross_series':frame.loc[candidate,'series_id']=s['request'].dataset.series_ids[-1]
        elif fault=='future_event':frame.loc[candidate,'period_end']=s['b']['at'](7)
        elif fault=='late':frame.loc[candidate,'available_at']=s['b']['at'](7)
        elif fault=='revision':frame.loc[candidate,'value']=999999.
        else:frame['value']=frame['value'].astype(object)
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',forbidden)
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(s['request'])


def test_future_values_and_source_order_do_not_change_snapshot(source,monkeypatch):
    s=source();before=s['service'].capture(s['request']);original=CanonicalTrainingPreparationService._capture
    def changed(*args,**kwargs):
        meta,ctx,frame,defs=original(*args,**kwargs)
        frame.loc[frame.period_end>s['b']['at'](6),'value']=999999999.
        frame=frame.iloc[::-1].reset_index(drop=True)
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    after=s['service'].capture(s['request'])
    assert before.canonical_bytes==after.canonical_bytes


@pytest.mark.parametrize('budget,value',[('max_captured_rows',16),('max_decoded_bytes',1),('max_output_bytes',1)])
def test_resource_bounds(source,budget,value):
    s=source();data=s['request'].model_dump();data['budgets'][budget]=value
    request=RuntimeSnapshotRequest.model_validate(data)
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(request)


@pytest.mark.parametrize('stage',['admit','read','after_read','return'])
@pytest.mark.parametrize('mode',['deny','cancel','deadline','exception'])
def test_callback_boundaries(source,stage,mode):
    s=source();control=s['control']
    if mode=='deny':control['deny']=stage
    else:
        def hook(current):
            if current!=stage:return
            if mode=='cancel':control['cancel']=True
            elif mode=='deadline':control['clock']=s['request'].budgets.max_elapsed_seconds
            else:raise RuntimeError('private callback payload')
        control['hook']=hook
    with pytest.raises(RuntimeSnapshotError) as caught:
        s['service'].capture(s['request'],cancelled=lambda:control['cancel'])
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert 'private callback payload' not in ''.join(traceback.format_exception(caught.value))


def test_lagged_covariates_same_capture(source,monkeypatch):
    s=source(exo=True);reads=[];original=s['b']['provider'].open_read
    @contextmanager
    def counted(*a,**kw):
        reads.append(1)
        with original(*a,**kw) as session:yield session
    monkeypatch.setattr(s['b']['provider'],'open_read',counted)
    result=s['service'].capture(s['request'])
    assert len(reads)==1 and len(result.external)==4 and len(result.historical_covariates)==16
    assert dict(result.source_dtypes)['promotion']
    for value in result.historical_covariates:
        assert value.value==s['ledger'][value.source_record_id][2].offer
    assert all(v.field=='promotion' and v.value=='B' and v.period_start==s['b']['at'](5) for v in result.external)
    assert all(v.available_at<=result.request.capture_cutoff for v in result.external)


@pytest.mark.parametrize('field',['history','static','external','historical_covariates','source_dtypes','source_schema_digest','target_period_end'])
def test_direct_snapshot_substitution_rejects(source,field):
    s=source(exo=True);snapshot=s['service'].capture(s['request']);data=json.loads(snapshot.canonical_json)
    if field=='history':data[field].reverse()
    elif field=='static':data[field][0]['dimensions'][0][1]='wrong'
    elif field=='external':data[field][0]['available_at']='2099-01-01T00:00:00Z'
    elif field=='historical_covariates':data[field].reverse()
    elif field=='source_dtypes':data[field].reverse()
    elif field=='source_schema_digest':data[field]='0'*64
    else:data[field]='2099-01-01T00:00:00Z'
    with pytest.raises(Exception):RuntimeInputSnapshot.model_validate_json(json.dumps(data))


def test_secret_diagnostics_and_duplicate_json(source,caplog,capsys):
    s=source();secret='R702SEC_8264';data=s['request'].model_dump()
    data['feature_schema_json']=secret
    with warnings.catch_warnings(record=True) as observed:
        with pytest.raises(Exception) as caught:RuntimeSnapshotRequest.model_validate(data)
    rendered=''.join(traceback.format_exception(caught.value))+repr(caught.value)+str(caught.value.args)
    output=capsys.readouterr()
    assert secret not in rendered+output.err+caplog.text+str([str(w.message) for w in observed])
    encoded=s['request'].canonical_json
    with pytest.raises(Exception):RuntimeSnapshotRequest.model_validate_json(encoded[:-1]+',"version":"1.0"}')


@pytest.mark.parametrize('rule',['known_in_advance','external_forecast'])
def test_authorized_future_covariates_not_future_actuals(source,monkeypatch,rule):
    s=source(exo=True,rule=rule);seen=[]
    def approved(req):
        seen.append(req)
        return (req.period_start==s['request'].forecast_origin and req.value=='B'
                and req.available_at<=s['request'].capture_cutoff and req.field=='promotion')
    args=dict(s['args'],authorize_external_forecast=approved)
    service=CanonicalRuntimeSnapshotService(s['b']['provider'],**args)
    original=CanonicalTrainingPreparationService._capture
    def changed(*a,**kw):
        meta,ctx,frame,defs=original(*a,**kw)
        frame.loc[frame.period_end>s['b']['at'](6),'value']=999999999.
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    result=service.capture(s['request'])
    assert all(c.period_start==s['request'].forecast_origin and c.value=='B' for c in result.external)
    assert all(h.value!=999999999. for h in result.history)
    assert len(seen)==(8 if rule=='external_forecast' else 0)  # creation + terminal recheck
    if rule=='external_forecast':
        with pytest.raises(RuntimeSnapshotError):s['service'].capture(s['request'])


@pytest.mark.parametrize('fault',['late','evidence','denied','later_revision'])
def test_future_covariate_failures(source,monkeypatch,fault):
    s=source(exo=True,rule='external_forecast');original=CanonicalTrainingPreparationService._capture
    def changed(*a,**kw):
        meta,ctx,frame,defs=original(*a,**kw);future=frame.period_start==s['request'].forecast_origin
        if fault=='late':frame.loc[future,'promotion_available_at']=s['b']['at'](7)
        elif fault=='evidence':frame.loc[future,'promotion_evidence']='substituted-release'
        elif fault=='later_revision':frame.loc[future,'promotion']='SUBSTITUTED'
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    def authorize(req):return fault!='denied' and req.value=='B'
    service=CanonicalRuntimeSnapshotService(s['b']['provider'],**dict(s['args'],authorize_external_forecast=authorize))
    with pytest.raises(RuntimeSnapshotError):service.capture(s['request'])


def test_store_payload_tampering_and_read_denial(source):
    s=source();provider=s['b']['provider'];provider.authorize_read=lambda *_:False
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(s['request'])
    provider.authorize_read=lambda *_:True
    payload=next(Path(s['b']['store'].root).rglob('*-canonical.parquet'))
    contents=payload.read_bytes();payload.chmod(0o600);payload.write_bytes(contents[:-1]+bytes([contents[-1]^1]))
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(s['request'])


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_accepted_frozen_preparation_contexts_without_refit(source,monkeypatch,family):
    from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
    from src.preprocessing.canonical_recurrent import RecurrentPreparationRequest,fit_recurrent_state
    from src.preprocessing.canonical_transformer import TransformerPreparationRequest,fit_transformer_state
    s=source(exo=family=='transformer',rule='known_in_advance' if family=='transformer' else None)
    b=s['b'];prepared=CanonicalTabularPreparationService(b['service']).prepare(b['plan'],b['configuration'])
    batches=prepared.outputs;base=batches[0].state
    common=dict(fold_id='fold-1',origins=(b['at'](7),),feature_names=('lag1',),
        window=dict(lookback=3,window=3,stride=1,past_offsets=(-3,-2,-1),padding='left'))
    if family in ('xgboost','lightgbm'):state=base
    elif family in ('lstm','gru'):
        state=fit_recurrent_state(base,batches,RecurrentPreparationRequest(model_family=family,**common))
    else:
        req=TransformerPreparationRequest(static_names=('location',),known_future_names=('promotion',),**common)
        state=fit_transformer_state(base,batches,req)
    data=s['request'].model_dump();data.update(forecast_origin=b['at'](7),capture_cutoff=b['at'](7),history_periods=7)
    request=RuntimeSnapshotRequest.model_validate(data)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',forbidden)
    checked=s['service'].validate_preparation_context(request,state,model_family=family)
    assert checked.classification=='SOURCE_CONTEXT_ONLY' and checked.preparation_state_id==state.state_id
    snapshot=s['service'].capture(request);assert len(snapshot.history)==28
    # Source-context admission does not claim to be an M2 tensor or native input.
    assert snapshot.classification=='DATA_READINESS_ONLY'
    if family not in ('xgboost','lightgbm'):
        with pytest.raises(RuntimeSnapshotError):s['service'].validate_preparation_context(s['request'],state,model_family=family)


def test_import_has_no_model_database_or_operational_edges(tmp_path):
    import os,subprocess,sys
    code='''
import builtins,socket
original=builtins.__import__
def trap(name,*a,**kw):
 if name.startswith(('src.inference','src.ml','src.database','src.deployment','xgboost','lightgbm','torch','tensorflow','mlflow','psycopg','redis')):raise AssertionError('operational import')
 return original(name,*a,**kw)
def denied(*a,**kw):raise AssertionError('socket')
builtins.__import__=trap;socket.socket.connect=denied;socket.create_connection=denied
import src.services.canonical_runtime_snapshot_service
print('SAFE')
'''
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path.cwd()))
    result=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='SAFE' and not list(tmp_path.iterdir())


def test_in_memory_repository_fake_single_snapshot_and_detachment(source):
    s=source();b=s['b'];original=b['store']
    from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
    from src.data.contracts.dataset import StagingBudgets
    with original._publication_session(StagingBudgets()):
        candidate=original._candidate(s['request'].dataset.reference,StagingBudgets())
        candidate.mapping_json=b['metadata'][0].mapping_json
        with original._read_snapshot(candidate,lambda:None) as batches:
            memory=tuple(frame.copy(deep=True) for frame in batches())
    class MemoryRepository:
        def __init__(self):self.reads=0
        def __getattr__(self,name):return getattr(original,name)
        @contextmanager
        def _read_snapshot(self,candidate,checkpoint):
            self.reads+=1
            owned=tuple(frame.copy(deep=True) for frame in memory)
            def batches():
                for frame in owned:
                    checkpoint();yield frame.copy(deep=True)
            yield batches
    repo=MemoryRepository()
    provider=CanonicalTrainingDataProvider(repo,authorize_read=b['provider'].authorize_read,clock=b['provider'].clock)
    service=CanonicalRuntimeSnapshotService(provider,**s['args'])
    snapshot=service.capture(s['request']);assert repo.reads==1
    frozen=snapshot.canonical_bytes
    for frame in memory:frame.loc[:,'value']=9999.
    assert snapshot.canonical_bytes==frozen
    with pytest.raises(RuntimeSnapshotError):service.capture(s['request'])


@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),2**54+1])
def test_invalid_numeric_source_values_reject(source,monkeypatch,bad):
    s=source();original=CanonicalTrainingPreparationService._capture
    def changed(*a,**kw):
        meta,ctx,frame,defs=original(*a,**kw)
        frame['value']=frame['value'].astype(object)
        idx=frame.index[frame.period_start.eq(s['b']['at'](3))][0]
        frame.loc[idx,'value']=bad
        return meta,ctx,frame,defs
    monkeypatch.setattr(CanonicalTrainingPreparationService,'_capture',changed)
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(s['request'])


def test_earlier_capture_cutoff_is_an_explicit_attester_input(source):
    s=source();data=s['request'].model_dump();data['capture_cutoff']=s['b']['at'](6)-pd.Timedelta(hours=1)
    request=RuntimeSnapshotRequest.model_validate(data)
    # The latest required actual was released at O; a cutoff before O cannot
    # silently fill or use it. The complete history request therefore rejects.
    with pytest.raises(RuntimeSnapshotError):s['service'].capture(request)


def test_past_exogenous_encoder_context_is_not_falsely_claimed(source):
    from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
    from src.preprocessing.canonical_recurrent import RecurrentPreparationRequest,fit_recurrent_state
    s=source(exo=True);b=s['b']
    batches=CanonicalTabularPreparationService(b['service']).prepare(b['plan'],b['configuration']).outputs
    req=RecurrentPreparationRequest(model_family='lstm',fold_id='fold-1',origins=(b['at'](7),),feature_names=('promotion',),
        window=dict(lookback=3,window=3,stride=1,past_offsets=(-3,-2,-1),padding='left'))
    state=fit_recurrent_state(batches[0].state,batches,req)
    data=s['request'].model_dump();data.update(forecast_origin=b['at'](7),capture_cutoff=b['at'](7),history_periods=7)
    request=RuntimeSnapshotRequest.model_validate(data)
    with pytest.raises(RuntimeSnapshotError):s['service'].validate_preparation_context(request,state,model_family='lstm')
