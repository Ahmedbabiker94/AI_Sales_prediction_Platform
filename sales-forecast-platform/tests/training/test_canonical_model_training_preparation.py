"""D1 owned boundary: synthetic admitted stores and independent source ledgers only."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from decimal import Decimal
import hashlib
import math
import runpy
import struct
import traceback
from uuid import UUID

import pandas as pd
import pytest

from src.data.contracts.dataset import StagingBudgets, operational_json
from src.data.contracts.features import FeatureConfiguration, FeatureState
from src.data.contracts.model_training import TrainingHandoffScope, TrainingHandoffProvenance
from src.data.contracts.temporal_preparation import TemporalPreparationPlan, TemporalFold, TemporalBudgets
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, TrainingPreparationError, ValueAttestedAvailabilityVerifier,
    TrainingLabelContext, CapturedHistoryClaim)


def identity(value):
    # Source-ledger calculation does not reuse the production claim builder.
    if isinstance(value,Decimal):
        t=value.as_tuple();return ('decimal',t.sign,tuple(t.digits),t.exponent)
    if type(value) is int:return ('integer',str(value))
    return ('float64',struct.pack('>d',float(value)).hex())


@pytest.fixture
def make_training(tmp_path):
    old=runpy.run_path('tests/training/test_temporal_training_preparation.py')
    make=old['make_temporal'].__wrapped__(tmp_path)
    def build(**kwargs):
        b=make(**kwargs);b['legacy']=b['service'];ledger={};trace=[];revoked=set();clock=[0.];cancel=[False]
        metadata={m.reference.organization_id:m for m in b['metadata']}
        for m in b['metadata']:
            raw=b['raws'][m.reference.organization_id.int]
            with b['provider'].open_read(m.reference,purpose='production_training') as session:
                records=[r for f in session.batches() for r in f.to_dict('records')]
            for r in records:
                source=raw.iloc[int(r['source_record_id'])]
                assert r['value']==source['reported']
                ledger[m.reference.organization_id,r['source_record_id']]=dict(
                    series=r['series_id'],lineage=r['lineage_id'],start=source['from'],end=source['to'],
                    release=source['released'],proof=source['proof'],value=identity(source['reported']),
                    revision=source['released'],category_release=source['promo_release'],category_proof=source['promo_proof'])
        def context(ctx):
            org=ctx.metadata.reference.organization_id
            assert metadata[org]==ctx.metadata and ctx.plan_digest==b['plan'].digest
            assert ctx.series_ids==next(d.series_ids for d in b['plan'].datasets if d.reference.organization_id==org)
            if ctx.fold_id:
                fold=next(f for f in b['plan'].folds if f.fold_id==ctx.fold_id)
                assert ctx.forecast_origin in fold.training_origins and ctx.training_cutoff==fold.categorical_cutoff
            return org
        def release(ctx,claims):
            org=context(ctx);trace.append(('release',org,ctx.fold_id,ctx.forecast_origin,claims))
            if ('release',org,ctx.fold_id,ctx.forecast_origin) in revoked:return False
            for c in claims:
                t=ledger[org,c.source_record_id];cat=c.field!='value'
                if (c.series_id!=t['series'] or c.lineage_id!=t['lineage'] or c.period_start!=t['start']
                        or c.period_end!=t['end'] or c.available_at!=t['category_release' if cat else 'release']
                        or c.evidence_reference!=t['category_proof' if cat else 'proof']):return False
            return True
        def value(ctx,claims):
            org=context(ctx);trace.append(('value',org,ctx.fold_id,ctx.forecast_origin,claims));m=metadata[org]
            for c in claims:
                a=c.availability;t=ledger[org,a.source_record_id];mapping=m.mapping
                if (type(c) is not CapturedHistoryClaim or a.value_identity!=t['value'] or c.dataset_reference!=m.reference
                        or c.manifest_digest!=m.manifest_digest or c.admission_digest!=m.admission_digest
                        or c.mapping_digest!=hashlib.sha256(m.mapping_json.encode()).hexdigest()
                        or c.source_id!=m.source_id or c.mapping_id!=mapping.mapping_id or c.mapping_version!=mapping.version
                        or (c.metric,c.unit,c.currency,c.purpose)!=(mapping.target_metric,mapping.measurement.unit,mapping.measurement.currency,'production_training')
                        or c.organization_id!=org or c.series_ids!=ctx.series_ids or c.plan_digest!=b['plan'].digest
                        or c.fold_id!=ctx.fold_id or c.forecast_origin!=ctx.forecast_origin
                        or c.calibration_cutoff!=ctx.training_cutoff or c.target_period_end!=ctx.target_period_end):return False
                for cutoff in (ctx.forecast_origin,ctx.training_cutoff):
                    if cutoff is not None and a.period_end<=cutoff and a.available_at<=cutoff and t['revision']>cutoff:return False
            return True
        def labels(ctx,claims):
            org=context(ctx);trace.append(('label',org,ctx.fold_id,ctx.forecast_origin,claims))
            if ('label',org,ctx.fold_id,ctx.forecast_origin) in revoked:return False
            assert type(ctx) is TrainingLabelContext and ctx.training_scope_digest==b['scope'].digest
            f=next(f for f in b['plan'].folds if f.fold_id==ctx.fold_id)
            assert ctx.model_fit_cutoff==f.model_fit_cutoff and ctx.action=='verify_training_label_at_M'
            m=metadata[org];defs={s.series_id:s for s in m.series}
            for c in claims:
                t=ledger[org,c.source_record_id]
                if (c.dataset!=next(d for d in b['plan'].datasets if d.reference.organization_id==org)
                        or c.plan_digest!=b['plan'].digest or c.fold_id!=ctx.fold_id or c.series_id!=t['series']
                        or c.lineage_id!=t['lineage'] or c.forecast_origin!=ctx.forecast_origin
                        or c.period_start!=t['start'] or c.period_end!=t['end'] or c.available_at!=t['release']
                        or c.evidence_reference!=t['proof'] or c.model_fit_cutoff!=f.model_fit_cutoff
                        or c.original_value_identity_json!=operational_json(t['value']) or t['revision']>f.model_fit_cutoff
                        or c.dimensions!=tuple(sorted(defs[c.series_id].dimensions.items()))
                        or (c.metric,c.unit,c.currency,c.frequency)!=(m.mapping.target_metric,m.mapping.measurement.unit,m.mapping.measurement.currency,b['plan'].frequency)):
                    return False
            return True
        def authorize(ctx,stage):
            org=context(ctx);trace.append((stage,org,ctx.fold_id,ctx.forecast_origin,None))
            return ('auth',org,ctx.fold_id,ctx.forecast_origin) not in revoked
        b.update(ledger=ledger,trace=trace,revoked=revoked,clock=clock,cancel=cancel,
                 release=release,value=value,label_authority=labels,authorize=authorize)
        def bootstrap(**changes):
            scope=TrainingHandoffScope(scope_id='operator-training-v1',datasets=b['plan'].datasets,
                configuration_digest=b['plan'].configuration_digest,label_authority_id='independent-source-ledger',
                label_authority_version='synthetic-1',**changes.pop('scope_changes',{}))
            b['scope']=scope
            opts=dict(authorize_preparation=authorize,
                verify_availability=ValueAttestedAvailabilityVerifier(verify_availability=release,verify_captured_values=value),
                training_scope=scope,verify_training_labels=labels,elapsed_clock=lambda:clock[0])
            opts.update(changes)
            b['service']=CanonicalTrainingPreparationService(b['provider'],**opts)
            return b['service']
        b['bootstrap']=bootstrap;bootstrap();return b
    return build


def prepare(b,**kwargs):
    return b['service'].prepare_training(b['plan'],b['configuration'],cancelled=lambda:b['cancel'][0],**kwargs)


def fold_change(b,**changes):
    d=b['plan'].model_dump();f=d['folds'][0];f.update(changes);d['folds']=(f,)
    b['plan']=TemporalPreparationPlan.model_validate(d)


def test_owned_training_only_hand_calculated_handoff_preserves_original_plan_and_state(make_training,monkeypatch):
    b=make_training(exo=True);fitted=[];inputs=[];fit=CanonicalFeaturePipeline.fit_state;execute=CanonicalFeaturePipeline.prepare_training
    def fitting(self,frame,**kw):
        assert frame.period_end.max()<=kw['training_cutoff']
        state=fit(self,frame,**kw);fitted.append(state.model_dump_json());return state
    def executing(self,frame,*args,**kw):
        assert frame.period_end.max()<=kw['forecast_origin']
        assert frame.available_at.max()<=kw['forecast_origin'];inputs.append(kw['forecast_origin'])
        return execute(self,frame,*args,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fitting)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_training',executing)
    result=prepare(b);assert len(result.origins)==2 and len(result.matrix)==4
    assert result.status=='PREPARED' and result.model_status=='NOT_MODEL_FITTED'
    assert result.provenance.plan==b['plan'] and result.plan_json==b['plan'].model_dump_json()
    assert inputs==list(b['plan'].folds[0].training_origins) and len(fitted)==1
    assert result.provenance.states[0].state_json==fitted[0]
    assert all(g.preparation.state_json==fitted[0] and g.partition=='training' for g in result.origins)
    north=next(s.series_id for s in b['metadata'][0].series if s.dimensions['location']=='north')
    one,two=result.origins
    assert one.preparation.matrix.loc[(UUID(int=2),north,b['at'](4)),['lag1','mean4','std4']].tolist()==[8.,1.5,5.]
    row=two.preparation.matrix.loc[(UUID(int=2),north,b['at'](5))]
    assert row.lag1==16 and row.mean4==5 and row.std4==pytest.approx(math.sqrt(236/3))
    assert one.preparation.labels.loc[(UUID(int=2),north,b['at'](4))]==16.
    assert two.preparation.labels.loc[(UUID(int=2),north,b['at'](5))]==32.
    assert all(g.preparation.fitted_state.vocabularies['promotion']==('A',) for g in result.origins)
    assert one.preparation.matrix.promotion.eq(0).all() and two.preparation.matrix.promotion.eq(-1).all()
    assert TrainingHandoffProvenance.model_validate_json(result.provenance.model_dump_json())==result.provenance
    label_calls=[x for x in b['trace'] if x[0]=='label'];assert len(label_calls)==4
    assert all(label_calls[i][4] is label_calls[i+2][4] for i in range(2))
    assert prepare(b).handoff_id==result.handoff_id


def test_heldout_values_never_converted_or_exposed_and_future_assessment_is_not_executed(make_training,monkeypatch):
    def change(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-17',tz='UTC'),'reported']=Decimal('0.1')
    b=make_training(decimal=True,raw_change=change);seen=[];original=CanonicalFeaturePipeline.convert_numeric
    def convert(self,value,*args):seen.append(value);return original(self,value,*args)
    monkeypatch.setattr(CanonicalFeaturePipeline,'convert_numeric',convert)
    fold_change(b,assessment_cutoff=pd.Timestamp('2099-01-01',tz='UTC'))
    result=prepare(b)
    assert Decimal('0.1') not in seen and len(result.original_labels)==4
    assert all(c.forecast_origin in b['plan'].folds[0].training_origins for c in result.provenance.labels)


@pytest.mark.parametrize('partition',['validation','test'])
def test_same_date_partition_injection_rejects_before_capture(make_training,partition):
    b=make_training();d=b['plan'].model_dump();d['folds'][0][partition+'_origins']=(b['at'](5),)
    forged=TemporalPreparationPlan.model_construct(**{**b['plan'].__dict__,'folds':(
        TemporalFold.model_construct(**d['folds'][0]),)})
    with pytest.raises(TrainingPreparationError):b['service'].prepare_training(forged,b['configuration'])
    assert not b['trace']


@pytest.mark.parametrize('fault',['late','revision','substitution','missing_authority'])
def test_label_value_revision_and_release_must_be_independently_verified_at_M(make_training,fault):
    b=make_training();org=UUID(int=2)
    if fault=='late':b['ledger'][org,'5']['release']=b['at'](7)
    if fault=='revision':b['ledger'][org,'5']['revision']=b['at'](7)
    if fault=='substitution':b['ledger'][org,'5']['value']=identity(999.)
    if fault=='missing_authority':b['bootstrap'](verify_training_labels=lambda *_:False)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_label_release_after_O_and_by_M_is_valid_but_never_becomes_earlier_feature(make_training):
    def change(raw):
        for n in (5,17):raw.loc[n,'released']=pd.Timestamp('2020-02-17',tz='UTC')
    b=make_training(raw_change=change);r=prepare(b)
    assert r.provenance.labels[-1].available_at==b['at'](6)>b['at'](5)
    assert sorted(r.origins[1].preparation.matrix.lag1)==[16.,116.]


@pytest.mark.parametrize('fault',['category_late','category_evidence','history_release','revision_at_O'])
def test_category_C_and_feature_O_evidence_rejects_before_handoff(make_training,monkeypatch,fault):
    def change(raw):
        if fault=='category_late':raw.loc[0,'promo_release']=pd.Timestamp('2020-02-10',tz='UTC')
        if fault=='category_evidence':raw.loc[0,'promo_proof']=None
        if fault=='history_release':raw.loc[3,'released']=pd.Timestamp('2020-02-10',tz='UTC')
    b=make_training(exo=True,raw_change=change);fits=[];old=CanonicalFeaturePipeline.fit_state
    if fault=='revision_at_O':b['ledger'][UUID(int=2),'3']['revision']=b['at'](5)
    def fit(self,*a,**kw):fits.append(1);return old(self,*a,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert not fits


@pytest.mark.parametrize('fault',['fold','organization','configuration'])
def test_fitted_state_substitution_rejects(make_training,monkeypatch,fault):
    b=make_training();old=CanonicalFeaturePipeline.fit_state
    def fit(self,*a,**kw):
        state=old(self,*a,**kw);d=state.model_dump()
        if fault=='fold':d['training_partition_reference']='f'*64
        if fault=='organization':d['organization_ids']=(UUID(int=3),)
        if fault=='configuration':d['configuration_digest']='f'*64
        return FeatureState.model_validate(d)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_folds_and_organizations_export_distinct_actual_states(make_training):
    b=make_training(exo=True,organizations=(2,3));d=b['plan'].model_dump()
    second=dict(d['folds'][0],fold_id='fold-2',training_origins=(b['at'](5),b['at'](6)),validation_origins=(b['at'](8),),
        test_origins=(),categorical_cutoff=b['at'](5),model_fit_cutoff=b['at'](7),assessment_cutoff=b['at'](9))
    d['folds']=(*d['folds'],second);b['plan']=TemporalPreparationPlan.model_validate(d)
    result=prepare(b);states=result.provenance.states
    assert len(states)==4 and len({s.state.state_id for s in states})==4
    for s in states:
        assert s.state.organization_ids==(s.dataset.reference.organization_id,)
        assert s.state.vocabularies['promotion']==(('A',) if s.fold_id=='fold-1' else ('A','B'))
    assert len(result.origins)==8
    tampered=result.provenance.model_dump();tampered['states'][1]['state_json']=states[0].state_json
    with pytest.raises(ValueError):TrainingHandoffProvenance.model_validate(tampered)


@pytest.mark.parametrize('fault',['tenant','mapping','version','purpose','digest','result','dictionary'])
def test_caller_scope_replay_or_claimed_provenance_grants_no_permission(make_training,fault):
    b=make_training();p=b['plan'];d=p.model_dump()
    if fault=='purpose':d['purpose']='offline_forecast_evaluation'
    elif fault=='digest':d['configuration_digest']='f'*64
    elif fault=='tenant':d['datasets'][0]['reference']=type(p.datasets[0].reference)(UUID(int=3),p.datasets[0].reference.dataset_id,p.datasets[0].reference.attempt_id,p.datasets[0].reference.version_id)
    elif fault=='mapping':d['datasets'][0]['mapping_id']='other-mapping'
    elif fault=='version':
        ref=p.datasets[0].reference;d['datasets'][0]['reference']=type(ref)(ref.organization_id,ref.dataset_id,ref.attempt_id,'f'*64);d['datasets'][0]['manifest_digest']='f'*64
    elif fault=='result':d=prepare(b)
    if fault not in ('result','dictionary'):
        try:d=TemporalPreparationPlan.model_validate(d)
        except ValueError:return
    with pytest.raises(TrainingPreparationError):b['service'].prepare_training(d,b['configuration'])


@pytest.mark.parametrize('fault',['weak_history','missing_labels','missing_scope'])
def test_opt_in_requires_fixed_authorities_not_legacy_boolean_verifier(make_training,fault):
    b=make_training();options=dict(training_scope=b['scope'],verify_training_labels=b['label_authority'],
        verify_availability=ValueAttestedAvailabilityVerifier(verify_availability=b['release'],verify_captured_values=b['value']))
    options[{'weak_history':'verify_availability','missing_labels':'verify_training_labels','missing_scope':'training_scope'}[fault]]=(lambda *_:True) if fault=='weak_history' else None
    with pytest.raises(TrainingPreparationError):CanonicalTrainingPreparationService(b['provider'],authorize_preparation=b['authorize'],**options)


@pytest.mark.parametrize('phase',['after_read','return','return_training_labels'])
@pytest.mark.parametrize('failure',['deny','cancel','deadline','secret'])
def test_late_owned_boundary_failure_returns_nothing_sanitized(make_training,phase,failure):
    b=make_training();original=b['authorize']
    def auth(ctx,stage):
        if stage==phase:
            if failure=='deny':return False
            if failure=='cancel':b['cancel'][0]=True
            if failure=='deadline':b['clock'][0]=300.
            if failure=='secret':raise RuntimeError('synthetic-private-token')
        return original(ctx,stage)
    b['bootstrap'](authorize_preparation=auth)
    try:prepare(b)
    except TrainingPreparationError as error:
        assert 'synthetic-private-token' not in ''.join(traceback.format_exception(error))
    else:pytest.fail('late failure returned training inputs')
    with b['store']._publication_session(StagingBudgets()):pass


def test_deadline_below_boundary_and_normal_final_callbacks_succeed(make_training):
    b=make_training();old=b['authorize']
    def auth(ctx,stage):
        if stage=='return_training_labels':b['clock'][0]=299.999
        return old(ctx,stage)
    b['bootstrap'](authorize_preparation=auth);assert prepare(b).status=='PREPARED'


@pytest.mark.parametrize('fault',['nan','backwards'])
def test_invalid_clock_fails_closed(make_training,fault):
    b=make_training();old=b['authorize']
    def auth(ctx,stage):
        if stage=='return_training_labels':b['clock'][0]=float('nan') if fault=='nan' else -1.
        return old(ctx,stage)
    b['bootstrap'](authorize_preparation=auth)
    with pytest.raises(TrainingPreparationError):prepare(b)


@pytest.mark.parametrize('fault',['cleanup','late_read'])
def test_successful_reader_close_required_before_any_fitting(make_training,monkeypatch,fault):
    b=make_training();opened=b['provider'].open_read;fits=[]
    @contextmanager
    def broken(*a,**kw):
        with opened(*a,**kw) as session:
            if fault=='late_read':
                stream=session.batches
                def broken_batches():
                    for f in stream():yield f
                    raise OSError('synthetic-read-secret')
                session.batches=broken_batches
            yield session
        if fault=='cleanup':raise OSError('synthetic-close-secret')
    b['provider'].open_read=broken
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',lambda *_a,**_kw:fits.append(1))
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert not fits
    with b['store']._publication_session(StagingBudgets()):pass


def test_one_complete_capture_lock_release_and_detached_immutable_results(make_training,monkeypatch):
    b=make_training(decimal=True);opened=b['provider'].open_read;active=[False];reads=[];old=CanonicalFeaturePipeline.fit_state
    @contextmanager
    def track(*a,**kw):
        active[0]=True;reads.append(1)
        try:
            with opened(*a,**kw) as s:
                yield s
                assert s.completed
        finally:active[0]=False
    b['provider'].open_read=track
    def fit(self,*a,**kw):
        assert not active[0]
        with b['store']._publication_session(StagingBudgets()):pass
        return old(self,*a,**kw)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',fit)
    r=prepare(b);assert len(reads)==1
    before=r.matrix;labels=r.labels;state=r.provenance.states[0].state
    detached_matrix=r.matrix;detached_labels=r.labels
    detached_matrix.iloc[:,:]=7;detached_labels.iloc[:]=7
    pd.testing.assert_frame_equal(before,r.matrix);pd.testing.assert_series_equal(labels,r.labels)
    assert all(type(v) is Decimal for v in r.original_labels)
    with pytest.raises(FrozenInstanceError):r.origins=()
    with pytest.raises(ValueError):r.provenance.model_status='FITTED'
    assert state.state_id==r.origins[0].preparation.fitted_state.state_id
    assert prepare(b).original_labels==r.original_labels


@pytest.mark.parametrize('cap',['max_output_cells','max_work_cells','max_fitted_state_bytes','max_captured_rows'])
def test_aggregate_temporal_limits_remain_active(make_training,cap):
    b=make_training();d=b['plan'].model_dump();d['budgets'][cap]=1
    b['plan']=TemporalPreparationPlan.model_validate(d)
    with pytest.raises(TrainingPreparationError):prepare(b)


@pytest.mark.parametrize('cap',['max_verification_calls','max_verification_claims','max_verification_bytes','max_handoff_bytes'])
def test_operator_owned_verification_and_handoff_limits(make_training,cap):
    b=make_training();b['bootstrap'](scope_changes={cap:1})
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_default_strict_exact_rejects_fractional_training_label_without_modifying_original(make_training):
    def change(raw):raw.loc[5,'reported']=Decimal('0.1')
    b=make_training(decimal=True,raw_change=change)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert b['raws'][2].iloc[5].reported==Decimal('0.1000')


def test_legacy_single_and_temporal_entry_points_remain_available(make_training):
    b=make_training();legacy=b['legacy'];assert len(legacy.prepare_temporal(b['plan'],b['configuration']).origins)==4
    with pytest.raises(TrainingPreparationError):legacy.prepare_training(b['plan'],b['configuration'])
    assert len(prepare(b).origins)==2


@pytest.mark.parametrize('state',['staging','sealed','aborted','rejected'])
def test_only_finalized_training_publications_enter_owned_boundary(make_training,state):
    import sqlite3
    b=make_training()
    with sqlite3.connect(b['store'].root/'catalog.sqlite') as conn:
        conn.execute('UPDATE attempts SET state=?',(state,))
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_late_canonical_chunk_corruption_returns_no_handoff_or_feature_state(make_training,monkeypatch):
    b=make_training();ref=b['plan'].datasets[0].reference
    chunks=sorted((b['store'].root/str(ref.attempt_id)).glob('*-canonical.parquet'))
    assert len(chunks)>1;chunks[-1].chmod(0o600);chunks[-1].write_bytes(b'synthetic-corruption')
    fitted=[];monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',lambda *_a,**_k:fitted.append(1))
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert not fitted


@pytest.mark.parametrize('source',['global','read','label'])
def test_current_origin_and_label_authority_revocation_is_rechecked(make_training,source):
    b=make_training();org=UUID(int=2);origin=b['at'](5);base=b['authorize'];read=b['provider'].require_current
    def revoke():b['revoked'].add(('label' if source=='label' else 'auth',org,'fold-1',origin))
    def auth(ctx,stage):
        if stage=='return' and ctx.fold_id is None and source in ('global','label'):revoke()
        return base(ctx,stage)
    def current(*a,**kw):
        answer=read(*a,**kw)
        if source=='read' and any(x[0]=='label' for x in b['trace']):revoke()
        return answer
    b['provider'].require_current=current;b['bootstrap'](authorize_preparation=auth)
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert any(x[0]=='label' and x[3]==origin for x in b['trace'])


def test_bootstrap_references_cannot_be_replaced_through_legacy_mutable_slots(make_training):
    b=make_training();b['service'].authorize=lambda *_:False;b['service'].verify_availability=lambda *_:False
    assert prepare(b).status=='PREPARED'
    b['revoked'].add(('auth',UUID(int=2),'fold-1',b['at'](5)))
    b['service'].authorize=lambda *_:True
    with pytest.raises(TrainingPreparationError):prepare(b)


def test_signed_zero_and_negative_training_labels_remain_exact(make_training):
    def change(raw):raw.loc[4,'reported']=-0.;raw.loc[5,'reported']=-16.
    b=make_training(raw_change=change);r=prepare(b)
    zeros=[v for v in r.original_labels if v==0.]
    assert len(zeros)==1 and math.copysign(1.,zeros[0])==-1
    assert any(v==-16. for v in r.original_labels)
    assert any(c.original_value_identity_json==operational_json(identity(-0.)) for c in r.provenance.labels)


def test_training_label_revision_after_O_but_by_M_and_not_in_history_succeeds(make_training):
    b=make_training();b['ledger'][UUID(int=2),'5']['revision']=b['at'](6)
    assert len(prepare(b).origins)==2


def test_explicit_synthetic_bounded_policy_requires_new_bootstrap_identity(make_training):
    def change(raw):raw.loc[5,'reported']=Decimal('0.1')
    b=make_training(decimal=True,raw_change=change);original=b['plan'].configuration_digest
    b['mapping']=b['metadata'][0].mapping
    runpy.run_path('tests/training/test_canonical_training_preparation.py')['bounded'](b)
    d=b['plan'].model_dump();d['configuration_digest']=CanonicalFeaturePipeline(b['configuration']).digest
    b['plan']=TemporalPreparationPlan.model_validate(d)
    with pytest.raises(TrainingPreparationError):prepare(b)
    b['bootstrap']();r=prepare(b)
    assert r.provenance.configuration_digest!=original
    assert any(v==Decimal('0.1000') for v in r.original_labels)
    assert r.origins[0].preparation.schema.executor_version=='1.1.0'


def test_evaluation_publication_cannot_be_replayed_as_training(tmp_path):
    make=runpy.run_path('tests/training/test_deterministic_forecast_rollout.py')['make_rollout'].__wrapped__(tmp_path)
    b=make();data=b['plan'].model_dump()
    data.pop('predictor');data.pop('budgets');data.pop('version');data['purpose']='production_training'
    data['forecast_horizon']=1;data['capability']='supervised_h1'
    plan=TemporalPreparationPlan.model_validate(data)
    scope=TrainingHandoffScope(scope_id='synthetic-purpose-boundary',datasets=plan.datasets,
        configuration_digest=plan.configuration_digest,label_authority_id='synthetic-label-authority',label_authority_version='1')
    # Even deliberately permissive synthetic authority callbacks cannot turn an
    # evaluation publication into a training publication at the provider boundary.
    service=CanonicalTrainingPreparationService(b['provider'],authorize_preparation=lambda *_:True,
        verify_availability=ValueAttestedAvailabilityVerifier(verify_availability=lambda *_:True,verify_captured_values=lambda *_:True),
        training_scope=scope,verify_training_labels=lambda *_:True)
    with pytest.raises(TrainingPreparationError):service.prepare_training(plan,b['configuration'])


def test_verification_budget_counts_release_and_value_authorities_separately(make_training):
    b=make_training();b['bootstrap'](scope_changes={'max_verification_calls':3})
    # read + after_read + release exhaust the budget. The value authority is
    # refused before invocation, rather than hidden inside one adapter charge.
    with pytest.raises(TrainingPreparationError):prepare(b)
    assert any(t[0]=='release' for t in b['trace'])
    assert not any(t[0]=='value' for t in b['trace'])
