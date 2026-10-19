"""Actual bounded CPU XGBoost fits; isolated synthetic FINALIZED publications only."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from decimal import Decimal
import base64
import json
import runpy
import struct
import traceback

import numpy as np
import pandas as pd
import pytest

from src.data.contracts.dataset import operational_json,StagingBudgets
from src.data.contracts.features import FeatureConfiguration
from src.data.contracts.temporal_preparation import TemporalPreparationPlan
from src.data.contracts.model_candidate import (OfflineXGBoostConfiguration,TrainingInvocation,CandidateBundle,
    CandidateManifest,digest,strict_float32)
from src.services.canonical_training_preparation_service import ValueAttestedAvailabilityVerifier,CanonicalTrainingPreparationService
from src.services.canonical_xgboost_training_service import CanonicalXGBoostTrainingService as Service,CandidateTrainingError


@pytest.fixture
def make_owner(tmp_path):
    d1=runpy.run_path('tests/training/test_canonical_model_training_preparation.py')['make_training'].__wrapped__(tmp_path)
    def make(*,rows_change=None,training=None,all_features=False,**changes):
        b=d1(raw_change=rows_change)
        cfg=b['configuration'].model_dump()
        if not all_features:
            # Explicit approved fixture representation. Irrational std4 values
            # would violate D2's strict model-specific float32 ingress policy.
            cfg['configuration_id']='offline-xgb-fixture';cfg['version']='2.1'
            cfg['ordered_outputs']=tuple(n for n in cfg['ordered_outputs'] if n!='std4')
            cfg['calculations']=tuple(c for c in cfg['calculations'] if c['output']!='std4')
            cfg['missing_value_behavior'].pop('std4');cfg['output_dtypes'].pop('std4')
        b['configuration']=FeatureConfiguration.model_validate(cfg)
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        p=b['plan'].model_dump();p.update(configuration_id=b['configuration'].configuration_id,
            configuration_version=b['configuration'].version,configuration_digest=CanonicalFeaturePipeline(b['configuration']).digest)
        b['plan']=TemporalPreparationPlan.model_validate(p);b['bootstrap']()
        events=[];fault=[None]
        def authorization(ctx,stage):
            events.append((stage,ctx))
            inv=TrainingInvocation.model_validate_json(ctx.invocation_json)
            assert inv.fixture_approval_id=='synthetic-fixture-approval'
            if ctx.provenance_json:
                from src.data.contracts.model_training import TrainingHandoffProvenance
                proof=TrainingHandoffProvenance.model_validate_json(ctx.provenance_json)
                assert proof.plan==b['plan'] and proof.scope_digest==b['scope'].digest
            if ctx.preparation:
                assert ctx.preparation.fold_id=='fold-1'
                assert ctx.preparation.forecast_origin in b['plan'].folds[0].training_origins
            if stage=='return' and fault[0]=='revoke':
                b['revoked'].add(('auth',b['metadata'][0].reference.organization_id,'fold-1',b['at'](5)))
            if stage=='scoped_return':
                if fault[0]=='cancel':b['cancel'][0]=True
                if fault[0]=='deadline':b['clock'][0]=60.
                if fault[0]=='secret':raise RuntimeError('D2-SYNTHETIC-SECRET')
            return not (fault[0]=='deny' and stage=='return')
        args=dict(plan=b['plan'],configuration=b['configuration'],training_scope=b['scope'],
            invocation=TrainingInvocation(invocation_id='owned-synthetic-fit',fixture_approval_id='synthetic-fixture-approval',
                release_authority='synthetic-release-v1',category_authority='synthetic-release-v1',
                historical_value_authority='independent-value-v1',preparation_authority='synthetic-preparation-v1',training_authority='synthetic-fit-v1'),
            training=training or OfflineXGBoostConfiguration(),authorize_preparation=b['authorize'],
            verify_availability=ValueAttestedAvailabilityVerifier(verify_availability=b['release'],verify_captured_values=b['value']),
            verify_training_labels=b['label_authority'],authorize_training=authorization,elapsed_clock=lambda:b['clock'][0])
        args.update(changes);owner=Service(b['provider'],**args)
        b.update(owner=owner,options=args,events=events,fault=fault)
        b['train']=lambda:owner.train(cancelled=lambda:b['cancel'][0])
        return b
    return make


def test_genuine_native_fit_and_owned_roundtrip(make_owner,monkeypatch):
    import xgboost as xgb
    b=make_owner();fit=Service._fit_native;models=[];feature_fits=[]
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    state_fit=CanonicalFeaturePipeline.fit_state
    def feature_fit(self,*a,**kw):
        s=state_fit(self,*a,**kw);feature_fits.append(s.model_dump_json());return s
    def actual_fit(*a):
        with b['store']._publication_session(StagingBudgets()):pass
        m=fit(*a);assert type(m) is xgb.Booster;models.append(m);return m
    monkeypatch.setattr(Service,'_fit_native',staticmethod(actual_fit))
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',feature_fit)
    bundle=b['train']();manifest,artifact=bundle.validate()
    assert len(models)==1 and models[0].num_boosted_rounds()==8 and models[0].num_features()==3
    assert any('"split"' in tree for tree in models[0].get_dump(dump_format='json'))
    assert digest(artifact)==manifest.artifact_digest and len(artifact)==manifest.artifact_size
    assert feature_fits==[manifest.provenance.states[0].state_json]
    before=tuple(float(v) for v in np.frombuffer(bytes.fromhex(manifest.predictions_float32_hex),dtype='<f4'))
    assert before==b['owner'].verify_candidate(bundle)
    assert len(feature_fits)==1  # Loading/prediction never refits the feature state.
    assert len(before)==4 and len(set(before))>1
    assert manifest.status=='CANDIDATE' and manifest.production_status=='NOT_APPROVED_FOR_PRODUCTION'
    assert manifest.feature_names==('location','lag1','mean4')
    assert manifest.feature_dtypes==('int8','float64','float64')
    assert len(manifest.provenance.labels)==4 and all(g[4] in (b['at'](4).isoformat(),b['at'](5).isoformat()) for g in manifest.row_index)
    assert manifest.matrix.iloc[0].lag1 in (8.,108.)
    with pytest.raises(FrozenInstanceError):bundle.payload=b'changed'
    assert b['owner'].verify_candidate(bundle)==before
    # A canonical, byte-identical copy is still not this owner's loading receipt.
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(CandidateBundle(bundle.payload))


@pytest.mark.parametrize('case',['features','labels','state','order','index'])
def test_substituted_handoff_rejects_before_any_fit(make_owner,monkeypatch,case):
    from dataclasses import replace
    original=CanonicalTrainingPreparationService.prepare_training;b=make_owner();fits=[]
    def substituted(self,*a,**kw):
        handoff=original(self,*a,**kw);groups=list(handoff.origins);g=groups[0];p=g.preparation
        if case=='features':p=replace(p,feature_rows=tuple(tuple(999. for _ in r) for r in p.feature_rows))
        if case=='labels':p=replace(p,converted_labels=tuple(999. for _ in p.converted_labels))
        if case=='state':p=replace(p,state_json=handoff.origins[-1].preparation.state_json.replace('fold','foreign'))
        if case=='order':p=replace(p,feature_rows=tuple(tuple(reversed(r)) for r in p.feature_rows))
        if case=='index':p=replace(p,index=tuple(reversed(p.index)))
        groups[0]=replace(g,preparation=p)
        return replace(handoff,origins=tuple(groups))
    monkeypatch.setattr(CanonicalTrainingPreparationService,'prepare_training',substituted)
    monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    # "state" above might be byte-identical if no literal fold occurs in JSON.
    if case=='state':
        def wrong_state(self,*a,**kw):
            from dataclasses import replace
            h=original(self,*a,**kw);g=h.origins[0];d=json.loads(g.preparation.state_json);d['training_partition_reference']='f'*64
            p=replace(g.preparation,state_json=json.dumps(d));return replace(h,origins=(replace(g,preparation=p),*h.origins[1:]))
        monkeypatch.setattr(CanonicalTrainingPreparationService,'prepare_training',wrong_state)
    with pytest.raises(CandidateTrainingError):b['train']()
    assert not fits


def test_no_external_handoff_can_replace_owned_invocation(make_owner,monkeypatch):
    b=make_owner();h=b['service'].prepare_training(b['plan'],b['configuration']);fits=[]
    monkeypatch.setattr(CanonicalTrainingPreparationService,'prepare_training',lambda *_a,**_k:h)
    monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    with pytest.raises(CandidateTrainingError):b['train']()
    assert not fits
    with pytest.raises(TypeError):b['owner'].train(handoff=h)


@pytest.mark.parametrize('fault',['artifact','manifest','digest','state','columns','dtype','runtime','format','parameters','prediction','numeric','scope'])
def test_bundle_tampering_is_detected_and_never_grants_loading_permission(make_owner,fault):
    b=make_owner();original=b['train']();body=json.loads(original.payload);m=body['manifest']
    if fault=='artifact':
        data=bytearray(base64.b64decode(body['artifact_base64']));data[-1]^=1;body['artifact_base64']=base64.b64encode(data).decode()
    if fault=='manifest':m['status']='PRODUCTION'
    if fault=='digest':m['artifact_digest']='f'*64
    if fault=='state':m['state_digest']='f'*64
    if fault=='columns':m['feature_names']=list(reversed(m['feature_names']))
    if fault=='dtype':m['feature_dtypes'][0]='float64'
    if fault=='runtime':m['runtime']['xgboost']='1.0.0'
    if fault=='format':m['training']['serialization']='pickle'
    if fault=='parameters':m['parameters_json']='{}'
    if fault=='prediction':m['predictions_float32_hex']=np.zeros(len(m['row_index']),dtype='<f4').tobytes().hex()
    if fault=='numeric':m['matrix_float32_hex']=np.zeros((len(m['row_index']),len(m['feature_names'])),dtype='<f4').tobytes().hex()
    if fault=='scope':m['row_index'][0][1]='wrong-organization'
    bad=CandidateBundle(operational_json(body).encode())
    # Prediction metadata is structurally valid but not native-model-verified.
    # It still cannot be loaded: only the owner's original issued object can.
    if fault!='prediction':
        with pytest.raises((ValueError,TypeError)):bad.validate()
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(bad)
    assert b['owner'].verify_candidate(original)


@pytest.mark.parametrize('fault',['deny','revoke','cancel','deadline','secret'])
def test_late_authorization_or_checkpoint_failure_returns_no_candidate(make_owner,fault):
    b=make_owner();b['fault'][0]=fault
    with pytest.raises(CandidateTrainingError) as e:b['train']()
    assert 'D2-SYNTHETIC-SECRET' not in ''.join(traceback.format_exception(e.value))
    with b['store']._publication_session(StagingBudgets()):pass


@pytest.mark.parametrize('phase',['fit','save','load','predict'])
def test_native_failure_or_partial_serialization_never_issues_bundle(make_owner,monkeypatch,phase):
    import xgboost as xgb
    b=make_owner()
    def fail(*a,**k):raise RuntimeError('native-synthetic-secret')
    if phase=='fit':monkeypatch.setattr(Service,'_fit_native',staticmethod(fail))
    if phase=='save':monkeypatch.setattr(xgb.Booster,'save_raw',lambda *_a,**_k:bytearray(b'partial-ubj'))
    if phase=='load':monkeypatch.setattr(xgb.Booster,'load_model',fail)
    if phase=='predict':monkeypatch.setattr(Service,'_predict_native',staticmethod(fail))
    with pytest.raises(CandidateTrainingError) as e:b['train']()
    assert 'native-synthetic-secret' not in ''.join(traceback.format_exception(e.value))
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(CandidateBundle(b'{}'))


@pytest.mark.parametrize('field',['max_rows','max_cells','max_artifact_bytes','max_bundle_bytes','max_evidence_records','max_evidence_bytes'])
def test_training_and_evidence_budgets_fail_closed(make_owner,field):
    b=make_owner(training=OfflineXGBoostConfiguration(**{field:2 if field=='max_rows' else 1}))
    with pytest.raises(CandidateTrainingError):b['train']()


@pytest.mark.parametrize('fault',['cancel','deadline','secret'])
def test_native_fit_callback_failure_prevents_issuance(make_owner,monkeypatch,fault):
    b=make_owner();fit=Service._fit_native
    def altered(*args):
        if fault=='cancel':b['cancel'][0]=True
        if fault=='deadline':b['clock'][0]=60.
        if fault=='secret':raise RuntimeError('fit-private-value')
        return fit(*args)
    monkeypatch.setattr(Service,'_fit_native',staticmethod(altered))
    with pytest.raises(CandidateTrainingError) as e:b['train']()
    assert 'fit-private-value' not in ''.join(traceback.format_exception(e.value))


def test_reader_close_failure_prevents_xgboost_fit(make_owner,monkeypatch):
    b=make_owner();opened=b['provider'].open_read;fits=[]
    @contextmanager
    def fail_close(*a,**kw):
        with opened(*a,**kw) as s:yield s
        raise OSError('cleanup-synthetic-private')
    b['provider'].open_read=fail_close
    monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    with pytest.raises(CandidateTrainingError):b['train']()
    assert not fits
    with b['store']._publication_session(StagingBudgets()):pass


def test_unsupported_float32_feature_precision_rejects_before_fitting(make_owner,monkeypatch):
    b=make_owner(all_features=True);fits=[]
    monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    with pytest.raises(CandidateTrainingError):b['train']()
    assert not fits


def test_extreme_heldout_values_do_not_affect_training_rows_or_predictions(make_owner):
    first=make_owner();a=first['train']()
    def change(raw):raw.loc[raw['from']>=pd.Timestamp('2020-02-17',tz='UTC'),'reported']=999999999.
    second=make_owner(rows_change=change);b=second['train']()
    assert a.manifest.matrix_float32_hex==b.manifest.matrix_float32_hex
    assert a.manifest.labels_float32_hex==b.manifest.labels_float32_hex
    assert a.manifest.predictions_float32_hex==b.manifest.predictions_float32_hex
    assert a.manifest.provenance.plan.datasets!=b.manifest.provenance.plan.datasets


@pytest.mark.parametrize('alias',['production','models:/production/latest','models/production/current.json'])
def test_aliases_cannot_enter_native_loader(make_owner,alias):
    b=make_owner()
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(alias)
    with pytest.raises(TypeError):b['owner'].train(model_uri=alias)


@pytest.mark.parametrize('case',['partition','mixed_folds','mixed_organizations','horizon'])
def test_partition_and_scope_widening_reject_before_fitting(make_owner,tmp_path,monkeypatch,case):
    from src.data.contracts.temporal_preparation import TemporalFold
    b=make_owner();args=dict(b['options']);data=b['plan'].model_dump()
    if case=='partition':
        data['folds'][0]['validation_origins']=(b['at'](5),)
        fake=TemporalPreparationPlan.model_construct(**{**b['plan'].__dict__,
            'folds':(TemporalFold.model_construct(**data['folds'][0]),)})
        args['plan']=fake
    if case=='mixed_folds':
        data['folds']=(*data['folds'],dict(data['folds'][0],fold_id='other-fold',training_origins=(b['at'](5),b['at'](6)),
            calibration_start=b['at'](0),categorical_cutoff=b['at'](5),model_fit_cutoff=b['at'](7),
            validation_origins=(b['at'](8),),test_origins=(),assessment_cutoff=b['at'](9)))
        args['plan']=TemporalPreparationPlan.model_validate(data)
    if case=='mixed_organizations':
        multiple=runpy.run_path('tests/training/test_canonical_model_training_preparation.py')['make_training'].__wrapped__(tmp_path/'multiple')(organizations=(2,3))
        args.update(plan=multiple['plan'],configuration=multiple['configuration'],training_scope=multiple['scope'])
    if case=='horizon':
        data.update(capability='planning_only',forecast_horizon=2);args['plan']=TemporalPreparationPlan.model_validate(data)
    fits=[];monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    with pytest.raises(CandidateTrainingError):Service(b['provider'],**args)
    assert not fits


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,'not numeric'])
def test_bad_training_features_are_rejected_before_native_fit(make_owner,monkeypatch,bad):
    from dataclasses import replace
    b=make_owner();original=CanonicalTrainingPreparationService.prepare_training;fits=[]
    def alter(self,*a,**k):
        h=original(self,*a,**k);g=h.origins[0];p=g.preparation
        rows=list(p.feature_rows);rows[0]=(rows[0][0],bad,rows[0][2]);p=replace(p,feature_rows=tuple(rows))
        return replace(h,origins=(replace(g,preparation=p),*h.origins[1:]))
    monkeypatch.setattr(CanonicalTrainingPreparationService,'prepare_training',alter)
    monkeypatch.setattr(Service,'_fit_native',staticmethod(lambda *a:fits.append(1)))
    with pytest.raises(CandidateTrainingError):b['train']()
    assert not fits


def test_signed_labels_and_original_value_identities_survive(make_owner):
    def change(raw):raw.loc[4,'reported']=-0.;raw.loc[5,'reported']=-16.
    b=make_owner(rows_change=change);bundle=b['train']()
    raw=np.frombuffer(bytes.fromhex(bundle.manifest.labels_float32_hex),dtype='<f4')
    assert any(raw==-16.) and any((raw==0)&np.signbit(raw))
    claims=bundle.manifest.provenance.labels
    assert any(json.loads(c.original_value_identity_json)==['float64',struct.pack('>d',-0.).hex()] for c in claims)
    assert b['owner'].verify_candidate(bundle)


def test_issuance_single_use_and_foreign_owner_receipt_reject(make_owner):
    b=make_owner();bundle=b['train']()
    with pytest.raises(CandidateTrainingError):b['train']()
    other=make_owner()
    with pytest.raises(CandidateTrainingError):other['owner'].verify_candidate(bundle)


def test_prediction_verification_reauthorizes_and_checks_deadline(make_owner):
    b=make_owner();bundle=b['train']();before=b['owner'].verify_candidate(bundle)
    b['fault'][0]='cancel'
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(bundle,cancelled=lambda:b['cancel'][0])
    assert tuple(float(x) for x in np.frombuffer(bytes.fromhex(bundle.manifest.predictions_float32_hex),dtype='<f4'))==before


@pytest.mark.parametrize('case',['nan','infinite','boolean','float64','shape'])
def test_invalid_native_predictions_reject(make_owner,monkeypatch,case):
    import xgboost as xgb
    b=make_owner()
    def bad(self,data,*a,**kw):
        n=data.num_row()
        if case=='nan':return np.full(n,np.nan,dtype=np.float32)
        if case=='infinite':return np.full(n,np.inf,dtype=np.float32)
        if case=='boolean':return np.ones(n,dtype=bool)
        if case=='float64':return np.zeros(n,dtype=np.float64)
        return np.zeros((n,1),dtype=np.float32)
    monkeypatch.setattr(xgb.Booster,'predict',bad)
    with pytest.raises(CandidateTrainingError):b['train']()


def test_independent_native_api_reloads_actual_bundle_bytes(make_owner):
    import xgboost as xgb
    b=make_owner();bundle=b['train']();manifest,artifact=bundle.validate()
    direct=xgb.Booster(params={'nthread':1})
    direct.load_model(bytearray(artifact))
    rows=manifest.matrix.to_numpy(dtype=np.float32)
    predictions=direct.predict(xgb.DMatrix(rows,feature_names=list(manifest.feature_names),nthread=1))
    assert predictions.astype('<f4').tobytes().hex()==manifest.predictions_float32_hex
    assert direct.num_boosted_rounds()==8 and direct.num_features()==3
    assert tuple(direct.feature_names)==manifest.feature_names
    assert direct.attributes()['state']==digest(manifest.provenance.states[0].state_json.encode())
    assert direct.attributes()['d1_handoff']==manifest.provenance.handoff_id
    assert len(direct.get_dump(dump_format='json'))==8
    # No score calculation and no held-out row or label is involved here.
    assert len(rows)==4 and {r[0] for r in manifest.row_index}=={'fold-1'}
