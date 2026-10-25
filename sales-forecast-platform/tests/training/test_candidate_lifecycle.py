"""M9 real M6/M7/M8 composition, local publications and estimator stubs only."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import timedelta
import hashlib,json,runpy,traceback,warnings
from unittest.mock import Mock
import pytest
from src.data.contracts.candidate_lifecycle import *
from src.data.contracts.candidate_evaluation import CandidateEvaluationRequest
from src.services.candidate_lifecycle_service import (bootstrap_candidate_lifecycle,ActorCapability,LifecycleHandle,EvidenceHandle,PrecommitHandle)
from src.repositories.candidate_lifecycle_repository import verify_lifecycle_audit


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    generator=runpy.run_path('tests/training/test_candidate_evaluation.py')['setup'].__wrapped__(tmp_path)
    make=next(generator)
    def build(*,families=('xgboost','lightgbm')):
        b=make(families=families);now=b['service'].provider.clock();time={'now':now}
        sources=tuple(sorted(m.candidate_id for m in b['manifests']))
        policy=LifecyclePolicy(authority=AuthorityContext(authority_id='m9-local-test-operator',bootstrap_context=digest('trusted-host')),
            organization_id=b['plan'].datasets[0].reference.organization_id,dataset_id=b['plan'].datasets[0].reference.dataset_id,
            valid_from=now-timedelta(hours=1),expires_at=now+timedelta(hours=1),assessment_cutoff=b['plan'].folds[0].assessment_cutoff)
        operator,service=bootstrap_candidate_lifecycle(b['evaluator'],policy,clock=lambda:time['now'])
        def actor(name='reviewer',roles=('administrator','approver','reviewer'),ids=sources,seconds=1800):
            return operator.grant(ActorAssertion(actor_id=name,identity_context=digest(name),roles=roles,candidate_ids=ids,
                valid_from=now-timedelta(minutes=1),expires_at=now+timedelta(seconds=seconds)))
        principal=actor()
        registration=RegistrationRequest(request_id=digest('registration'),candidate_id=sources[0],policy_id=policy.policy_id)
        life,outcome=service.register(principal,registration)
        def capture():return service.capture(principal,life,b['request'])
        def request(action,evidence=None,key=None,**kw):
            state=service.inspect(life)
            reasons={'VERIFY_EVIDENCE':'VERIFIED_TEST_ONLY_EVIDENCE','REQUEST_REVIEW':'REQUEST_OFFLINE_REVIEW',
                'APPROVE':'ALLOW_FURTHER_VALIDATION_ONLY','REJECT':'OPERATOR_REJECTION','REVOKE':'AUTHORITY_WITHDRAWN'}
            d=dict(request_id=digest(key or action),lifecycle_id=state.lifecycle_id,expected_version=state.state_version,
                policy_id=policy.policy_id,action=action,reason=reasons[action],evidence_id=evidence.evidence_id if evidence else None)
            d.update(kw);return DecisionRequest(**d)
        b.update(policy=policy,operator=operator,lifecycle=service,principal=principal,life=life,registration=registration,
            register_outcome=outcome,time=time,actor=actor,capture=capture,decision=request)
        return b
    yield build
    try:next(generator)
    except StopIteration:pass


def approve(b):
    handle,ref=b['capture']()
    for action in ('VERIFY_EVIDENCE','REQUEST_REVIEW','APPROVE'):
        out=b['lifecycle'].decide(b['principal'],b['life'],b['decision'](action,ref),evidence=handle)
    return handle,ref,out


def test_original_owned_state_machine_and_audit(setup):
    b=setup();handle,ref,out=approve(b);service=b['lifecycle']
    assert out.current.state=='APPROVED_FOR_FURTHER_VALIDATION' and out.current.state_version==4
    decision=service.require_further_validation_decision(b['life'])
    assert decision.transition_id==out.transition.transition_id
    assert decision.reason=='ALLOW_FURTHER_VALIDATION_ONLY' and decision.evidence.classification=='TEST_ONLY_NON_DEPLOYABLE'
    audit=service.audit();assert len(audit)==4
    assert verify_lifecycle_audit(audit,audit[-1].transition_id)==audit[-1].transition_id
    assert tuple(r.after for r in audit)==('CANDIDATE_REGISTERED','EVIDENCE_VERIFIED','REVIEW_PENDING','APPROVED_FOR_FURTHER_VALIDATION')
    assert TransitionRecord.model_validate_json(decision.canonical_json).canonical_bytes==decision.canonical_bytes
    state=service.inspect(b['life'])
    with pytest.raises(Exception):state.state='REVOKED'


@pytest.mark.parametrize('fault',['skip','stale','reason','policy','fake_actor','actor_string','scope','role','fake_evidence','detached_report'])
def test_invalid_authority_and_decisions_leave_ledger_unchanged(setup,fault):
    b=setup();service=b['lifecycle'];h,ref=b['capture']();actor=b['principal'];eh=h
    req=b['decision']('VERIFY_EVIDENCE',ref)
    if fault=='skip':req=b['decision']('APPROVE',ref)
    elif fault=='stale':req=req.model_copy(update={'expected_version':2})
    elif fault=='reason':req=req.model_copy(update={'reason':'ALLOW_FURTHER_VALIDATION_ONLY'})
    elif fault=='policy':req=req.model_copy(update={'policy_id':'f'*64})
    elif fault=='fake_actor':actor=ActorCapability()
    elif fault=='actor_string':actor='reviewer'
    elif fault=='scope':actor=b['actor']('limited',ids=(b['registration'].candidate_id,))
    elif fault=='role':actor=b['actor']('approver-only',roles=('approver',))
    elif fault=='fake_evidence':eh=EvidenceHandle()
    else:eh=b['evaluator'].evaluate(b['request'])
    before=service.audit()
    with pytest.raises(LifecycleError):service.decide(actor,b['life'],req,evidence=eh)
    assert service.audit()==before and service.inspect(b['life']).state_version==1


@pytest.mark.parametrize('fault',['actor_revoked','actor_expired','evidence_expired','package_tamper','discarded_candidate','revision_revoked','late_actor_revoke'])
def test_fresh_evidence_authority_and_original_ownership(setup,fault):
    b=setup();service=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    if fault=='actor_revoked':b['operator'].revoke_actor(b['principal'])
    elif fault=='actor_expired':b['time']['now']+=timedelta(minutes=30)
    elif fault=='evidence_expired':b['time']['now']+=timedelta(seconds=300)
    elif fault=='package_tamper':
        p=b['directory']/(b['manifests'][0].package_id+'.candidate');p.chmod(0o600);p.write_bytes(b'corrupt');p.chmod(0o400)
    elif fault=='discarded_candidate':b['trainer'].discard(b['results'][0].candidate)
    elif fault=='revision_revoked':b['flags']['revision']=False
    else:
        def callback(request,stage):
            if stage=='return':b['operator'].revoke_actor(b['principal'])
        b['flags']['callback']=callback
    with pytest.raises(LifecycleError):service.decide(b['principal'],b['life'],req,evidence=h)
    assert service.inspect(b['life']).state=='CANDIDATE_REGISTERED'


def test_idempotency_conflicting_duplicate_and_terminal_nonrevival(setup):
    b=setup();s=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    first=s.decide(b['principal'],b['life'],req,evidence=h);second=s.decide(b['principal'],b['life'],req,evidence=h)
    assert second.status=='IDEMPOTENT' and first.transition==second.transition and len(s.audit())==2
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],req.model_copy(update={'reason':'OPERATOR_REJECTION'}),evidence=h)
    for action in ('REQUEST_REVIEW','APPROVE'):s.decide(b['principal'],b['life'],b['decision'](action,ref),evidence=h)
    approval=b['decision']('APPROVE',ref,expected_version=3)
    s.decide(b['principal'],b['life'],b['decision']('REVOKE'))
    replay=s.decide(b['principal'],b['life'],approval,evidence=h)
    assert replay.status=='IDEMPOTENT' and replay.current.state=='REVOKED'
    with pytest.raises(LifecycleError):s.require_further_validation_decision(b['life'])
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],b['decision']('APPROVE',ref,key='reactivate'),evidence=h)


def test_atomic_failure_and_concurrent_cas(setup,monkeypatch):
    b=setup();s=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    repo=s._root.provider._repository;old=repo._data
    with monkeypatch.context() as patch:
        patch.setattr(repo,'_swap',Mock(side_effect=OSError('PRIVATE_FAILURE')))
        with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],req,evidence=h)
    assert repo._data is old and len(s.audit())==1
    different=req.model_copy(update={'request_id':digest('competitor')})
    def run(request):
        try:return s.decide(b['principal'],b['life'],request,evidence=h).status
        except LifecycleError:return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,(req,different)))
    assert sorted(results)==['APPLIED','DENIED'] and len(s.audit())==2


def test_rejection_new_explicit_lifecycle_and_revocation_of_unusable_evidence(setup):
    b=setup();s=b['lifecycle'];s.decide(b['principal'],b['life'],b['decision']('REJECT'))
    req=b['registration'].model_copy(update={'request_id':digest('new-generation')})
    with pytest.raises(LifecycleError):s.register(b['principal'],req)
    req=req.model_copy(update={'previous_lifecycle_id':s.inspect(b['life']).lifecycle_id})
    new,out=s.register(b['principal'],req)
    assert out.current.previous_lifecycle_id==s.inspect(b['life']).lifecycle_id
    assert out.current.state=='CANDIDATE_REGISTERED' and out.current.lifecycle_id!=s.inspect(b['life']).lifecycle_id
    b['trainer'].discard(b['results'][0].candidate)
    state=s.inspect(new)
    revoke=DecisionRequest(request_id=digest('withdraw'),lifecycle_id=state.lifecycle_id,expected_version=1,
        policy_id=b['policy'].policy_id,action='REVOKE',reason='EVIDENCE_INVALIDATED')
    assert s.decide(b['principal'],new,revoke).current.state=='REVOKED'


def test_precommit_local_order_conflict_single_use_and_test_approval(setup):
    b=setup();s=b['lifecycle'];h,ref=b['capture']()
    d=b['request'].model_dump(mode='json');d['partition']='test'
    for k in d['keys']:
        k['partition']='test';k['scope'].update(forecast_origin=b['at'](7).isoformat(),target_period=b['at'](7).isoformat(),target_period_end=b['at'](8).isoformat())
    for batch in d['batches']:
        for p,k in zip(batch['observations'],d['keys']):p['key']=k
    test=CandidateEvaluationRequest.model_validate_json(json.dumps(d))
    with pytest.raises(LifecycleError):s.capture(b['principal'],b['life'],test)
    pre=s.precommit(b['principal'],b['life'],h,test)
    assert s.precommit(b['principal'],b['life'],h,test) is pre
    conflicting=test.model_dump(mode='json');conflicting['ensemble'].update(mode='fixed_mean_v1',weights=[{'numerator':'1','denominator':'4'},{'numerator':'3','denominator':'4'}])
    with pytest.raises(LifecycleError):s.precommit(b['principal'],b['life'],h,conflicting)
    test_handle,test_ref=s.capture(b['principal'],b['life'],test,precommit=pre)
    assert test_ref.precommit_id is not None and test_ref.partition=='test'
    p=b['operator'].precommit_audit()[0];disclosure=b['operator'].disclosure_audit()[0]
    assert ref.local_sequence<p.local_sequence<disclosure.local_sequence<test_ref.local_sequence
    assert disclosure.precommit_id==p.precommit_id==test_ref.precommit_id
    with pytest.raises(LifecycleError):s.capture(b['principal'],b['life'],test,precommit=pre)
    with pytest.raises(LifecycleError):s.precommit(b['principal'],b['life'],h,test)
    for action in ('VERIFY_EVIDENCE','REQUEST_REVIEW','APPROVE'):
        out=s.decide(b['principal'],b['life'],b['decision'](action,test_ref),evidence=test_handle)
    assert out.current.state=='APPROVED_FOR_FURTHER_VALIDATION'
    assert s.require_further_validation_decision(b['life']).evidence.precommit_id==test_ref.precommit_id


def test_failed_test_capture_consumes_precommit_and_prevents_local_recommit(setup):
    b=setup();s=b['lifecycle'];h,ref=b['capture']();d=b['request'].model_dump(mode='json');d['partition']='test'
    for k in d['keys']:
        k['partition']='test';k['scope'].update(forecast_origin=b['at'](7).isoformat(),target_period=b['at'](7).isoformat(),target_period_end=b['at'](8).isoformat())
    for batch in d['batches']:
        for p,k in zip(batch['observations'],d['keys']):p['key']=k
    test=CandidateEvaluationRequest.model_validate_json(json.dumps(d));pre=s.precommit(b['principal'],b['life'],h,test)
    b['flags']['labels']=False
    with pytest.raises(LifecycleError):s.capture(b['principal'],b['life'],test,precommit=pre)
    b['flags']['labels']=True
    with pytest.raises(LifecycleError):s.capture(b['principal'],b['life'],test,precommit=pre)
    with pytest.raises(LifecycleError):s.precommit(b['principal'],b['life'],h,test)
    assert s.inspect(b['life']).state_version==1


@pytest.mark.parametrize('claim',['GLOBAL_PRETEST_VERIFIED','LEAKAGE_SAFE_TEST_SELECTION'])
def test_global_pretest_claims_fail_closed(setup,claim):
    b=setup();d=b['policy'].model_dump(mode='json');d['selection_assurance']=claim
    with pytest.raises(LifecycleError):bootstrap_candidate_lifecycle(b['evaluator'],d,clock=lambda:b['time']['now'])
    d=b['decision']('REVOKE').model_dump(mode='json');d['selection_claim']=claim
    with pytest.raises(LifecycleError):b['lifecycle'].decide(b['principal'],b['life'],d)


@pytest.mark.parametrize('target',['actor','lifecycle','evidence','operator','service'])
def test_foreign_copied_or_reconstructed_authority_rejects(setup,target):
    b=setup();s=b['lifecycle'];h,ref=b['capture']()
    if target=='actor':
        with pytest.raises(TypeError):copy(b['principal'])
        with pytest.raises(LifecycleError):s.decide(ActorCapability(),b['life'],b['decision']('VERIFY_EVIDENCE',ref),evidence=h)
    elif target=='lifecycle':
        with pytest.raises(LifecycleError):s.inspect(LifecycleHandle())
    elif target=='evidence':
        with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],b['decision']('VERIFY_EVIDENCE',ref),evidence=EvidenceHandle())
    elif target=='operator':
        with pytest.raises(LifecycleError):copy(b['operator']).grant({})
    else:
        with pytest.raises(LifecycleError):copy(s).inspect(b['life'])


def test_foreign_registry_and_detached_transition_grant_no_approval(setup):
    b=setup();_,_,out=approve(b)
    other_operator,other=bootstrap_candidate_lifecycle(b['evaluator'],b['policy'],clock=lambda:b['time']['now'])
    with pytest.raises(LifecycleError):other.inspect(b['life'])
    with pytest.raises(LifecycleError):other.require_further_validation_decision(out.transition)
    with pytest.raises(LifecycleError):b['lifecycle'].require_further_validation_decision(out.transition)


def test_authority_scope_failure_precedes_label_reads(setup,monkeypatch):
    b=setup();h,ref=b['capture']();limited=b['actor']('limited-reader',ids=(b['registration'].candidate_id,))
    spy=Mock(side_effect=AssertionError('no unauthorized label access'));monkeypatch.setattr(b['label_provider'],'open_read',spy)
    with pytest.raises(LifecycleError):b['lifecycle'].decide(limited,b['life'],b['decision']('VERIFY_EVIDENCE',ref),evidence=h)
    with pytest.raises(LifecycleError):b['lifecycle'].describe_evidence(limited,h,b['life'])
    spy.assert_not_called()


def test_audit_integrity_rejects_truncation_reordering_and_substitution(setup):
    b=setup();_,_,out=approve(b);records=b['lifecycle'].audit();head=records[-1].transition_id
    for corrupted in (records[:-1],tuple(reversed(records)),records[:-1]+(records[-1].model_copy(update={'reason':'OPERATOR_REJECTION'}),)):
        with pytest.raises(LifecycleError):verify_lifecycle_audit(corrupted,head)
    events=b['operator'].authority_audit()
    assert len(events)==1 and events[0].grant.policy_id==b['policy'].policy_id and events[0].previous==ZERO
    b['operator'].revoke_actor(b['principal'])
    events=b['operator'].authority_audit();assert events[-1].previous==events[0].event_id
    with pytest.raises(LifecycleError):b['lifecycle'].require_further_validation_decision(b['life'])


@pytest.mark.parametrize('field,value',[('version','9.0'),('policy_id','f'*64),('reason','SECRET_REASON'),('expected_version',True)])
def test_guarded_request_rejection_without_diagnostics_or_audit_change(setup,capsys,caplog,field,value):
    b=setup();d=b['decision']('REVOKE').model_dump(mode='json');d[field]=value
    secret='M9_SECRET_KEY_'+digest(field)[:12];secret_value='M9_SECRET_VALUE_'+digest(str(value))[:12];d[secret]=secret_value
    before=b['lifecycle'].audit()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(LifecycleError) as caught:b['lifecycle'].decide(b['principal'],b['life'],d)
        warnings.warn('unrelated warning',UserWarning)
    exc=caught.value;diagnostics=str(exc)+repr(exc)+repr(exc.args)+''.join(traceback.format_exception(exc))+caplog.text+str(capsys.readouterr())+str([str(w.message) for w in seen])
    assert secret not in diagnostics and secret_value not in diagnostics
    assert exc.__cause__ is None and exc.__context__ is None
    assert [str(w.message) for w in seen]==['unrelated warning'] and b['lifecycle'].audit()==before


def test_no_native_model_operations_or_production_modules_during_decisions(setup,monkeypatch):
    b=setup();from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    from src.training.canonical_adapters import NativeBackend
    monkeypatch.setattr(CanonicalFeaturePipeline,'fit_state',Mock(side_effect=AssertionError('no fit')))
    monkeypatch.setattr(NativeBackend,'__init__',Mock(side_effect=AssertionError('no native framework')))
    import builtins
    old=builtins.__import__
    def trap(name,*a,**kw):
        if name.startswith(('mlflow','torch','tensorflow','xgboost','lightgbm','src.audit_logger','src.deployment','src.ml.model_registry')):raise AssertionError('no production/framework import')
        return old(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',trap)
    _,_,out=approve(b)
    assert out.transition.after=='APPROVED_FOR_FURTHER_VALIDATION'


def test_reentrant_decisions_and_exact_post_callback_expiry_fail_closed(setup):
    b=setup();s=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    def expire(request,stage):
        if stage=='return':b['time']['now']=ref.expires_at
    b['flags']['callback']=expire
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],req,evidence=h)
    assert s.inspect(b['life']).state_version==1
    b=setup();s=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    def reenter(request,stage):
        if stage=='return':s.decide(b['principal'],b['life'],req,evidence=h)
    b['flags']['callback']=reenter
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],req,evidence=h)
    assert len(s.audit())==1


def test_latched_evidence_cannot_be_replaced_and_expired_approval_is_inactive(setup):
    b=setup();s=b['lifecycle'];h,ref=b['capture']()
    s.decide(b['principal'],b['life'],b['decision']('VERIFY_EVIDENCE',ref),evidence=h)
    other,other_ref=b['capture']()
    assert other_ref.evidence_id!=ref.evidence_id
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],b['decision']('REQUEST_REVIEW',other_ref),evidence=other)
    for action in ('REQUEST_REVIEW','APPROVE'):s.decide(b['principal'],b['life'],b['decision'](action,ref),evidence=h)
    b['time']['now']=ref.expires_at
    with pytest.raises(LifecycleError):s.require_further_validation_decision(b['life'])
    # Historical data remains inspectable; its state string is not a capability.
    assert s.inspect(b['life']).state=='APPROVED_FOR_FURTHER_VALIDATION'


def test_policy_and_resource_bounds(setup):
    b=setup();policy=b['policy'].model_copy(update={'max_transitions':1,'max_evidence':1})
    operator,service=bootstrap_candidate_lifecycle(b['evaluator'],policy,clock=lambda:b['time']['now'])
    assertion=b['operator'].authority_audit()[0].grant.assertion
    actor=operator.grant(assertion)
    registration=b['registration'].model_copy(update={'policy_id':policy.policy_id})
    life,_=service.register(actor,registration)
    h,ref=service.capture(actor,life,b['request'])
    with pytest.raises(LifecycleError):service.capture(actor,life,b['request'])
    req=DecisionRequest(request_id=digest('over-quota'),lifecycle_id=service.inspect(life).lifecycle_id,
        expected_version=1,policy_id=policy.policy_id,action='VERIFY_EVIDENCE',reason='VERIFIED_TEST_ONLY_EVIDENCE',evidence_id=ref.evidence_id)
    with pytest.raises(LifecycleError):service.decide(actor,life,req,evidence=h)
    assert len(service.audit())==1


def test_registration_idempotency_and_policy_scope_enforced(setup):
    b=setup();s=b['lifecycle'];again,out=s.register(b['principal'],b['registration'])
    assert again is b['life'] and out.status=='IDEMPOTENT' and len(s.audit())==1
    bad=b['policy'].model_dump(mode='json');bad['assessment_cutoff']=b['at'](9).isoformat()
    with pytest.raises(LifecycleError):bootstrap_candidate_lifecycle(b['evaluator'],bad,clock=lambda:b['time']['now'])
    bad=b['policy'].model_dump(mode='json');bad['organization_id']='00000000-0000-0000-0000-000000000999'
    with pytest.raises(LifecycleError):bootstrap_candidate_lifecycle(b['evaluator'],bad,clock=lambda:b['time']['now'])


def test_identity_guards_no_mutable_alias_and_no_secret_diagnostic(setup,capsys,caplog):
    b=setup();record=b['lifecycle'].audit()[0];original=record.canonical_bytes
    wire=record.model_dump(mode='json');wire['actor']['assertion']['roles'].append('administrator')
    assert record.canonical_bytes==original
    with pytest.raises(Exception):record.actor.assertion.actor_id='rewritten'
    assert ActorGrant.model_validate_json(record.actor.canonical_json).grant_id==record.actor.grant_id
    secret='M9_AUTH_SECRET_123451';d=b['policy'].model_dump(mode='json');d['authority'][secret]='M9_SECRET_VALUE_98123'
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(LifecycleError) as caught:bootstrap_candidate_lifecycle(b['evaluator'],d,clock=lambda:b['time']['now'])
    e=caught.value;text=str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+caplog.text+str(capsys.readouterr())+str(seen)
    assert secret not in text and 'M9_SECRET_VALUE_98123' not in text and not seen and e.__context__ is None and e.__cause__ is None


def test_all_five_families_remain_test_only_non_deployable(setup):
    b=setup(families=('xgboost','lightgbm','lstm','gru','transformer'))
    h,ref=b['capture']();out=b['lifecycle'].decide(b['principal'],b['life'],b['decision']('VERIFY_EVIDENCE',ref),evidence=h)
    assert len(ref.source_ids)==5 and ref.classification=='TEST_ONLY_NON_DEPLOYABLE'
    assert out.current.state=='EVIDENCE_VERIFIED'
    assert out.current.purpose=='CONTROLLED_OFFLINE_FURTHER_VALIDATION_DECISION_ONLY'


def test_failed_test_scope_cannot_claim_global_selection_even_after_new_lifecycle(setup):
    # Public policy vocabulary never exposes a globally leakage-safe claim.
    b=setup();req=b['decision']('REVOKE').model_dump(mode='json')
    for claim in ('GLOBAL_PRETEST_VERIFIED','PRODUCTION_APPROVED','NATIVE_MODEL_USE_AUTHORIZED'):
        changed=dict(req);changed['selection_claim']=claim
        with pytest.raises(LifecycleError):b['lifecycle'].decide(b['principal'],b['life'],changed)
    assert len(b['lifecycle'].audit())==1


@pytest.mark.parametrize('field,value',[('version','9.0'),('expected_version',True),('reason','UNKNOWN_REASON')])
def test_contract_fields_reject_without_extra_field_masking(field,value):
    request=DecisionRequest(request_id=digest('r'),lifecycle_id=digest('life'),expected_version=1,
        policy_id=digest('p'),action='REVOKE',reason='AUTHORITY_WITHDRAWN')
    d=request.model_dump(mode='json');d[field]=value
    from src.data.contracts.model_evaluation import EvaluationDeclarationError
    with pytest.raises(EvaluationDeclarationError):DecisionRequest.model_validate_json(json.dumps(d))


@pytest.mark.parametrize('fault',['revoke','expire'])
def test_idempotent_retry_rechecks_authority_after_last_source_callback(setup,monkeypatch,fault):
    b=setup();s=b['lifecycle'];h,ref=b['capture']();req=b['decision']('VERIFY_EVIDENCE',ref)
    first=s.decide(b['principal'],b['life'],req,evidence=h)
    original=s._root._source
    def source(cid):
        value=original(cid)
        if fault=='revoke':b['operator'].revoke_actor(b['principal'])
        else:b['time']['now']=ref.expires_at
        return value
    monkeypatch.setattr(s._root,'_source',source)
    with pytest.raises(LifecycleError):s.decide(b['principal'],b['life'],req,evidence=h)
    assert s.inspect(b['life'])==first.current and len(s.audit())==2
