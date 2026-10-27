"""R7-03 real M6/M7/M8/M9 and canonical source fixtures; estimator fits are stubs."""
from copy import copy
from contextlib import contextmanager
from datetime import timedelta
from dataclasses import replace
import hashlib
import json
import runpy
import traceback
from unittest.mock import Mock
import pytest

from src.data.contracts.dataset import operational_json
from src.data.contracts.model_representation import RepresentationContract
from src.data.contracts.runtime_snapshot import RuntimeSnapshotRequest
from src.data.contracts.runtime_model_use import RuntimeModelUseRequest,RuntimeModelUseDecision
from src.data.contracts.candidate_lifecycle import RegistrationRequest,DecisionRequest
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.services.runtime_model_use_evidence import RuntimeModelUseEvidence
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService
from src.services.canonical_training_preparation_service import ValueAttestedAvailabilityVerifier,historical_value_identity
from src.services.runtime_model_use_service import bootstrap_runtime_model_use,RuntimeInspectionCapability


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    prior=runpy.run_path('tests/training/test_candidate_lifecycle.py')
    generator=prior['setup'].__wrapped__(tmp_path);make=next(generator)
    def build(family='xgboost'):
        b=make(families=(family, 'lightgbm' if family!='lightgbm' else 'xgboost'))
        manifest=next(m for m in b['manifests'] if m.training.specification.family==family)
        cid=manifest.candidate_id
        if cid!=b['registration'].candidate_id:
            registration=RegistrationRequest(request_id=digest('runtime-registration'),candidate_id=cid,policy_id=b['policy'].policy_id)
            b['life'],_=b['lifecycle'].register(b['principal'],registration)
        def decision(action,evidence=None):
            state=b['lifecycle'].inspect(b['life'])
            reasons={'VERIFY_EVIDENCE':'VERIFIED_TEST_ONLY_EVIDENCE','REQUEST_REVIEW':'REQUEST_OFFLINE_REVIEW',
                     'APPROVE':'ALLOW_FURTHER_VALIDATION_ONLY','REVOKE':'AUTHORITY_WITHDRAWN'}
            return DecisionRequest(request_id=digest('runtime-'+action),lifecycle_id=state.lifecycle_id,
                expected_version=state.state_version,policy_id=b['policy'].policy_id,action=action,reason=reasons[action],
                evidence_id=evidence.evidence_id if evidence else None)
        b['decision']=decision
        eh,ref=b['lifecycle'].capture(b['principal'],b['life'],b['request'])
        for action in ('VERIFY_EVIDENCE','REQUEST_REVIEW','APPROVE'):
            out=b['lifecycle'].decide(b['principal'],b['life'],decision(action,ref),evidence=eh)
        binding=b['evaluator']._bindings[cid];packaging=binding.packaging
        record,_=packaging._owned(binding.candidate);state=record[4]
        base=state if manifest.training.capability.representation=='tabular' else state.encoder.base if manifest.training.specification.family=='transformer' else state.base
        canonical=b['service'].provider;meta=b['metadata'][0];raw=b['raws'][meta.reference.organization_id.int]
        ledger={}
        with canonical.open_read(meta.reference,purpose='production_training') as session:
            for frame in session.batches():
                for row in frame.to_dict('records'):
                    ledger[row['source_record_id']]=(row['series_id'],row['lineage_id'],raw.iloc[int(row['source_record_id'])])
        events=[];control={'cancel':False,'elapsed':0.,'on_return':None,'captures':0}
        def authorize(req,ctx,stage):
            events.append(stage)
            if stage=='return':
                control['captures']+=1
                if control['on_return']:control['on_return']()
            return True
        def availability(ctx,claims):
            if ctx.metadata!=meta:return False
            for c in claims:
                sid,lineage,row=ledger[c.source_record_id]
                if (c.series_id,c.lineage_id,c.period_start,c.period_end,c.available_at,c.evidence_reference)!=(sid,lineage,row['from'],row['to'],row['released' if c.field=='value' else 'promo_release'],row['proof' if c.field=='value' else 'promo_proof']):return False
                if c.available_at>ctx.capture_cutoff:return False
            return True
        def values(ctx,claims):
            return all(c.availability.value_identity==historical_value_identity(float(ledger[c.availability.source_record_id][2].reported)) and c.dataset_reference==meta.reference for c in claims)
        snapshots=CanonicalRuntimeSnapshotService(canonical,schema=base.canonical_schema,fitted_state=base.canonical_state,
            authorize_snapshot=authorize,verify_history=ValueAttestedAvailabilityVerifier(verify_availability=availability,verify_captured_values=values),elapsed_clock=lambda:control['elapsed'])
        sr=RuntimeSnapshotRequest(dataset=manifest.plan.datasets[0],purpose='production_training',forecast_origin=b['at'](7),capture_cutoff=b['at'](7),
            feature_schema_json=operational_json(base.canonical_schema.model_dump(mode='json')),history_periods=7,static_fields=('location','department'),external_fields=('promotion',))
        snapshot=snapshots.capture(sr)
        context=digest('r7_03_protected_host')
        req=RuntimeModelUseRequest(candidate_id=cid,package_id=manifest.package_id,family=manifest.training.specification.family,
            representation_binding_id=manifest.representation_binding_id,representation_compatibility_id=manifest.training.representation_compatibility_id,
            frozen_state_id=state.state_id,canonical_state_id=manifest.preprocessing.canonical_state_id,canonical_schema_id=manifest.preprocessing.canonical_schema_id,
            lifecycle_id=out.current.lifecycle_id,lifecycle_transition_id=out.transition.transition_id,evaluation_report_id=out.transition.evidence.report_id,
            ensemble_evaluation_id=out.transition.evidence.ensemble_evaluation_id,snapshot_id=snapshot.snapshot_id,snapshot_request=sr,host_context_id=context,actor_id='offline-operator')
        provider=RuntimeModelUseEvidence(packaging,binding.candidate,b['lifecycle'],b['life'],snapshots)
        operator,service=bootstrap_runtime_model_use(provider,host_context_id=context,clock=lambda:b['time']['now'],elapsed_clock=lambda:control['elapsed'])
        handle,receipt=operator.grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=120))
        control['captures']=0;events.clear()
        b.update(use_request=req,use_provider=provider,use_operator=operator,use_service=service,use_handle=handle,receipt=receipt,
            runtime_control=control,snapshots=snapshots,source_provider=canonical,source_events=events,retained=record,manifest=manifest,binding=binding)
        b['decide_use']=lambda req=req,handle=handle:service.decide(req,capability=handle,cancelled=lambda:control['cancel'])
        return b
    yield build
    try:next(generator)
    except StopIteration:pass


def trap(b,monkeypatch):
    forbidden=Mock(side_effect=AssertionError('operational edge forbidden'))
    monkeypatch.setattr(b['trainer'],'train',forbidden)
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',forbidden)
    monkeypatch.setattr(CanonicalFeaturePipeline,'prepare_inference',forbidden)
    import src.training.canonical_adapters as adapters
    monkeypatch.setattr(adapters.NativeBackend,'fit',forbidden)
    monkeypatch.setattr(adapters.NativeBackend,'__init__',forbidden)
    return forbidden


@pytest.mark.parametrize('family',['xgboost','lightgbm','lstm','gru','transformer'])
def test_real_chain_deny_only_all_families(setup,monkeypatch,family):
    b=setup(family);forbidden=trap(b,monkeypatch)
    reads=[];original=b['source_provider'].open_read
    @contextmanager
    def counted(*a,**kw):
        reads.append(1)
        with original(*a,**kw) as s:yield s
    monkeypatch.setattr(b['source_provider'],'open_read',counted)
    out=b['decide_use']()
    assert out.status=='UNAVAILABLE',out
    assert not out.executable_permission and not out.production_permission
    assert out.native_payload=='UNAVAILABLE' and out.evidence_classification=='TEST_ONLY_NON_DEPLOYABLE'
    assert out.model_input_compatibility=='SOURCE_CONTEXT_ONLY'
    assert b['runtime_control']['captures']==2 and len(reads)==2
    assert out.candidate_id==b['use_request'].candidate_id
    assert out.family==family
    assert b['decide_use']().canonical_bytes==out.canonical_bytes
    assert RuntimeModelUseDecision.model_validate_json(out.canonical_json)==out
    forbidden.assert_not_called()


@pytest.mark.parametrize('fault',['missing','forged','copied','receipt','foreign','revoked','expired','actor','context','scope'])
def test_inspection_authority_required_before_reads(setup,monkeypatch,fault):
    b=setup();handle=b['use_handle'];req=b['use_request']
    if fault=='missing':handle=None
    elif fault=='forged':handle=RuntimeInspectionCapability()
    elif fault=='copied':
        with pytest.raises(TypeError):copy(handle)
        assert b['runtime_control']['captures']==0
        return
    elif fault=='receipt':handle=b['receipt']
    elif fault=='foreign':
        op,_=bootstrap_runtime_model_use(b['use_provider'],host_context_id=req.host_context_id,clock=lambda:b['time']['now'])
        handle,_=op.grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
    elif fault=='revoked':b['use_operator'].revoke(handle)
    elif fault=='expired':b['time']['now']+=timedelta(seconds=120)
    elif fault=='actor':req=req.model_copy(update={'actor_id':'other'})
    elif fault=='context':req=req.model_copy(update={'host_context_id':digest('other')})
    else:req=req.model_copy(update={'snapshot_id':digest('other')})
    spy=Mock(side_effect=AssertionError('authority must reject before data read'))
    monkeypatch.setattr(b['snapshots'],'capture',spy)
    out=b['use_service'].decide(req,capability=handle)
    assert out.status=='DENIED' and out.candidate_id is None and out.verified_evidence_id is None
    spy.assert_not_called()


@pytest.mark.parametrize('field',['candidate_id','package_id','family','representation_binding_id','representation_compatibility_id','frozen_state_id',
    'canonical_state_id','canonical_schema_id','lifecycle_id','lifecycle_transition_id','evaluation_report_id','ensemble_evaluation_id','snapshot_id','purpose'])
def test_host_grant_does_not_bypass_binding_validation(setup,field):
    b=setup();req=b['use_request'];value='gru' if field=='family' else 'production_inference' if field=='purpose' else digest('substitution')
    req=req.model_copy(update={field:value})
    if field=='purpose':
        out=b['use_service'].decide(req,capability=b['use_handle'])
    else:
        h,_=b['use_operator'].grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
        out=b['use_service'].decide(req,capability=h)
    assert out.status=='DENIED' and out.verified_evidence_id is None


@pytest.mark.parametrize('fault',['m6_discard','m6_copy','package_tamper','m9_revoke','m9_actor_revoke','truth_revision','snapshot_publication','snapshot_origin','snapshot_tenant','snapshot_series'])
def test_original_evidence_required(setup,fault):
    b=setup();req=b['use_request']
    if fault=='m6_discard':b['trainer'].discard(b['binding'].candidate)
    elif fault=='m6_copy':b['use_provider']._candidate=copy(b['binding'].candidate)
    elif fault=='package_tamper':
        path=b['directory']/(req.package_id+'.candidate');path.chmod(0o600);path.write_bytes(b'corrupt');path.chmod(0o400)
    elif fault=='m9_revoke':b['lifecycle'].decide(b['principal'],b['life'],b['decision']('REVOKE'))
    elif fault=='m9_actor_revoke':b['operator'].revoke_actor(b['principal'])
    elif fault=='truth_revision':b['flags']['revision']=False
    else:
        sr=req.snapshot_request
        if fault=='snapshot_origin':sr=sr.model_copy(update={'forecast_origin':b['at'](6)})
        else:
            d=sr.dataset
            if fault=='snapshot_series':d=d.model_copy(update={'series_ids':tuple(reversed(d.series_ids))})
            elif fault=='snapshot_publication':d=d.model_copy(update={'admission_digest':digest('wrong')})
            else:
                from uuid import UUID
                d=d.model_copy(update={'reference':replace(d.reference,organization_id=UUID(int=999))})
            sr=sr.model_copy(update={'dataset':d})
        req=req.model_copy(update={'snapshot_request':sr})
    if req is not b['use_request']:
        try:h,_=b['use_operator'].grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
        except ValueError:return  # structurally invalid scope rejects at grant ingress
    else:h=b['use_handle']
    assert b['use_service'].decide(req,capability=h).status=='DENIED'


@pytest.mark.parametrize('fault',['host_revoke','m9_revoke','cancel','deadline','cap_expire','callback_error'])
def test_terminal_callback_denial_is_atomic(setup,monkeypatch,fault):
    b=setup();forbidden=trap(b,monkeypatch)
    def change():
        if b['runtime_control']['captures']!=2:return
        if fault=='host_revoke':b['use_operator'].revoke(b['use_handle'])
        elif fault=='m9_revoke':b['lifecycle'].decide(b['principal'],b['life'],b['decision']('REVOKE'))
        elif fault=='cancel':b['runtime_control']['cancel']=True
        elif fault=='deadline':b['runtime_control']['elapsed']=b['use_request'].limits.max_elapsed_seconds
        elif fault=='cap_expire':b['time']['now']+=timedelta(seconds=120)
        else:raise RuntimeError('R703_PRIVATE_SECRET_9171')
    b['runtime_control']['on_return']=change
    out=b['decide_use']()
    assert out.status=='DENIED' and out.verified_evidence_id is None and out.inspection_grant_id is None
    assert 'R703_PRIVATE_SECRET_9171' not in out.canonical_json
    forbidden.assert_not_called()


def test_m9_approval_alone_is_not_runtime_permission(setup):
    b=setup();assert b['lifecycle'].inspect(b['life']).state=='APPROVED_FOR_FURTHER_VALIDATION'
    out=b['use_service'].decide(b['use_request'])
    assert out.status=='DENIED' and not out.executable_permission


def test_busy_reentrancy_and_failure_has_no_cached_success(setup):
    b=setup();nested=[]
    b['runtime_control']['on_return']=lambda:nested.append(b['decide_use']())
    assert b['decide_use']().status=='UNAVAILABLE'
    assert all(o.status=='DENIED' and o.reason=='BUSY' for o in nested)
    b['runtime_control']['on_return']=None;b['use_operator'].revoke(b['use_handle'])
    assert b['decide_use']().status=='DENIED'


@pytest.mark.parametrize('fault',['nan_clock','backward_clock','output_limit','request_limit','cancel_initial','deadline_initial'])
def test_resource_and_clock_fail_closed(setup,fault):
    b=setup();req=b['use_request'];h=b['use_handle']
    if fault=='nan_clock':b['use_service']._root.elapsed=lambda:float('nan')
    elif fault=='backward_clock':
        values=iter([2.,1.]);b['use_service']._root.elapsed=lambda:next(values)
    elif fault=='cancel_initial':b['runtime_control']['cancel']=True
    elif fault=='deadline_initial':
        values=iter([0.,30.]);b['use_service']._root.elapsed=lambda:next(values)
    else:
        lim=req.limits.model_copy(update={'max_output_bytes' if fault=='output_limit' else 'max_request_bytes':1})
        req=req.model_copy(update={'limits':lim})
        if fault=='request_limit':
            assert b['use_service'].decide(req,capability=h).status=='DENIED';return
        h,_=b['use_operator'].grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
    out=b['use_service'].decide(req,capability=h,cancelled=lambda:b['runtime_control']['cancel'])
    assert out.status=='DENIED' and out.candidate_id is None


def test_contract_json_sanitization_and_no_positive_forgery(setup):
    b=setup();req=b['use_request']
    assert RuntimeModelUseRequest.model_validate_json(req.canonical_json).request_id==req.request_id
    for text in ('{"version":"1.0","version":"SECRET_703"}', '{"SECRET_703":', b'\xffSECRET_703'):
        try:RuntimeModelUseRequest.model_validate_json(text)
        except Exception as e:
            assert 'SECRET_703' not in ''.join(traceback.format_exception(e))
        else:raise AssertionError('invalid JSON admitted')
    with pytest.raises(Exception):RuntimeModelUseDecision(status='ELIGIBLE_FOR_CONTROLLED_VALIDATION',reason='INVALID_AUTHORITY_OR_EVIDENCE')
    with pytest.raises(Exception):RuntimeModelUseDecision(status='DENIED',reason='INVALID_AUTHORITY_OR_EVIDENCE',executable_permission=True)


@pytest.mark.parametrize('fault',['missing_package','pending_lifecycle','schema','cutoff','before_model_fit','reversed_features'])
def test_missing_and_incompatible_context(setup,fault):
    b=setup();req=b['use_request']
    if fault=='missing_package':
        path=b['directory']/(req.package_id+'.candidate');path.unlink()
    elif fault=='pending_lifecycle':
        # A fresh lifecycle for the second original candidate is not an approval.
        other=next(m for m in b['manifests'] if m.candidate_id!=req.candidate_id)
        root=b['lifecycle']._root
        existing=[h for h,lid in root.lives.values() if root.provider._state(lid).candidate.candidate_id==other.candidate_id]
        if existing:life=existing[0]
        else:life,_=b['lifecycle'].register(b['principal'],RegistrationRequest(request_id=digest('pending-runtime'),candidate_id=other.candidate_id,policy_id=b['policy'].policy_id))
        b['use_provider']._life=life
    else:
        sr=req.snapshot_request
        if fault=='schema':
            schema=json.loads(sr.feature_schema_json);schema['fitted_state_id']=digest('foreign-state')
            sr=sr.model_copy(update={'feature_schema_json':operational_json(schema)})
        elif fault=='reversed_features':
            schema=json.loads(sr.feature_schema_json);schema['configuration']['ordered_outputs'].reverse()
            sr=sr.model_copy(update={'feature_schema_json':operational_json(schema)})
        elif fault=='before_model_fit':
            sr=sr.model_copy(update={'forecast_origin':b['at'](5),'capture_cutoff':b['at'](5),'history_periods':5})
            # Valid original source context is still too early for this candidate.
            earlier=b['snapshots'].capture(sr)
            assert sr.forecast_origin < b['manifest'].plan.folds[0].model_fit_cutoff
            req=req.model_copy(update={'snapshot_id':earlier.snapshot_id})
        else:sr=sr.model_copy(update={'capture_cutoff':b['at'](8)})
        req=req.model_copy(update={'snapshot_request':sr})
    if req is b['use_request']:h=b['use_handle']
    else:
        try:h,_=b['use_operator'].grant_inspection(req,valid_from=b['time']['now']-timedelta(seconds=1),expires_at=b['time']['now']+timedelta(seconds=60))
        except ValueError:return
    assert b['use_service'].decide(req,capability=h).status=='DENIED'


def test_sanitized_callback_failure_has_no_diagnostic_output(setup,monkeypatch,capsys,caplog,recwarn):
    b=setup();trap(b,monkeypatch)
    def failure():raise RuntimeError('R703_CALLBACK_SECRET_5183')
    b['runtime_control']['on_return']=failure
    out=b['decide_use']()
    assert out.status=='DENIED'
    captured=capsys.readouterr()
    assert 'R703_CALLBACK_SECRET_5183' not in captured.err+captured.out+caplog.text+repr(out)
    assert not recwarn
