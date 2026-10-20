"""B0: genuine admitted publications, private operator ledger, no prediction."""
from copy import copy
from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
import json
import logging
import runpy
import traceback
from uuid import UUID, uuid4
import warnings

import pytest

from src.data.contracts.dataset import (StoredDatasetReference, DatasetAccessContext, ConsistencyPolicy,
    VerifiedConsistencyEvidence, ScopedWarningAuthorization, StagingBudgets)
from src.data.contracts.model_candidate import CandidateBundle
from src.data.contracts.model_evaluation import (ModelEvaluationEligibility, declare_evaluation_eligibility,
    PublicationRelationshipDeclaration)
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService
from src.services.canonical_xgboost_training_service import CandidateTrainingError
from src.services.training_evaluation_relationship_authority import (
    bootstrap_relationship_authority, RelationshipAuthorityError, RelationshipAuthorityBudgets,
    RelationshipApproval, RelationshipApprovalEvidence)


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    root = tmp_path_factory.mktemp('b0-genuine')
    make = runpy.run_path('tests/training/test_canonical_xgboost_training.py')['make_owner'].__wrapped__(root/'training')
    b = make(); bundle = b['train']()
    helpers = runpy.run_path('tests/data/contracts/test_model_evaluation.py')
    args = helpers['_genuine_arguments'](bundle)
    reference = runpy.run_path('tests/data/adapters/test_integration_foundation.py')
    training = b['metadata'][0]; mapping = training.mapping; now = b['provider'].clock()
    store = LocalDatasetRepository(root/'evaluation')
    provider = CanonicalTrainingDataProvider(store, authorize_read=lambda *_: True, clock=lambda: now)
    asset = reference['asset'].__wrapped__().model_copy(update={
        'organization_id': training.reference.organization_id, 'timezone': mapping.timezone})
    manifest = DatasetIngestionService(store, authorize_ingestion=lambda *_: True).ingest(
        reference['MemoryAdapter'](b['raws'][2], asset), mapping, dataset_id=UUID(int=800), batch_size=3)
    ref = StoredDatasetReference(manifest.organization_id, manifest.dataset_id, manifest.attempt_id, manifest.version_id)
    scope = DatasetAccessContext(ref, manifest.source_id, manifest.ingestion_batch_id, mapping.mapping_id,
        mapping.version, manifest.mapping_digest, mapping.target_metric, mapping.business_scope,
        'opaque-source', 'connection-1', 'offline_forecast_evaluation')
    evidence = VerifiedConsistencyEvidence(scope, 'snapshot', 'snapshot_external_v1', 'method-1', '1.0',
        now-timedelta(days=2), now-timedelta(hours=1), now-timedelta(minutes=1), now+timedelta(days=1))
    warning = ScopedWarningAuthorization(scope, 'NEGATIVE_VALUE', 'value', 'keep', 'b0-negative-approved',
        now-timedelta(hours=1), now+timedelta(days=1))
    DatasetFinalizationService(store, authorize=lambda *_: True, verify_consistency=lambda *_: True,
        verify_policy=lambda *_: True, verify_warning=lambda *_: True,
        decision_context_reference='b0-fixture-admission', clock=lambda: now).finalize(
            ref, mapping, connection_reference='connection-1', purpose='offline_forecast_evaluation',
            consistency_policy=ConsistencyPolicy(scope, 'SALES_GENERAL', '1.0.0'),
            consistency_evidence=evidence, warning_authorizations=(warning,))
    # Existing trusted acquisition fixture. B0 itself never opens a reader or
    # captures labels: it only uses require_current() against these metadata.
    with provider.open_read(ref, purpose='offline_forecast_evaluation') as session:
        evaluation = session.metadata
        for frame in session.batches(): pass
    binding = CanonicalTrainingPreparationService._temporal_binding(evaluation, args['relationship'].evaluation.series_ids)
    args['relationship'] = PublicationRelationshipDeclaration.model_validate({**args['relationship'].model_dump(),
        'evaluation': binding, 'authority_id': 'operator-fixture-ledger', 'authority_version': '1.0',
        'relationship_id': 'independent-operator-decision', 'evidence_references': ('decision-17',)})
    declaration = declare_evaluation_eligibility(bundle, **args)
    return dict(b=b, bundle=bundle, declaration=declaration, training=training, evaluation=evaluation,
        evaluation_provider=provider, evaluation_store=store, helpers=helpers)


@pytest.fixture
def setup(genuine):
    def create(**options):
        time = [0.]
        args = dict(authority_id='operator-fixture-ledger', authority_version='1.0',
            issuance_context='operator-reviewed-offline-fixture', elapsed_clock=lambda: time[0])
        args.update(options)
        operator, verifier = bootstrap_relationship_authority(genuine['b']['provider'], genuine['evaluation_provider'], **args)
        def issue(**changes):
            kwargs=dict(training_metadata=genuine['training'], evaluation_metadata=genuine['evaluation'],
                        approval_reference='decision-17')
            kwargs.update(changes)
            return operator.approve(genuine['declaration'], **kwargs)
        return operator, verifier, issue, time
    return create


def test_exact_operator_approval_and_fresh_publications(genuine, setup, monkeypatch):
    operator, verifier, issue, clock = setup(); events=[]
    from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
    def forbidden_features(*a,**kw):pytest.fail('B0 must not fit or execute feature state')
    for method in ('fit_state','prepare_training','prepare_inference'):
        monkeypatch.setattr(CanonicalFeaturePipeline,method,forbidden_features)
    for name, provider in [('training', genuine['b']['provider']), ('evaluation', genuine['evaluation_provider'])]:
        original=provider.require_current
        def checked(meta, *, budgets=None, old=original, name=name):
            events.append((name,meta.reference,meta.purpose));return old(meta,budgets=budgets)
        monkeypatch.setattr(provider,'require_current',checked)
        monkeypatch.setattr(provider,'open_read',lambda *a,**k:pytest.fail('B0 must not capture rows/labels'))
    handle=issue();receipt=verifier.verify(genuine['declaration'],handle)
    assert [e[0] for e in events]==['training','evaluation','training','evaluation']
    assert receipt.status=='RELATIONSHIP_ONLY' and receipt.issuance_context=='operator-reviewed-offline-fixture'
    assert receipt.approval_reference=='decision-17' and receipt.eligibility_id==genuine['declaration'].eligibility_id
    assert not hasattr(verifier,'approve') and not hasattr(verifier,'revoke')
    assert not vars(type(handle)).get('manifest')
    with pytest.raises(FrozenInstanceError):receipt.approval_id='forged'
    assert verifier.verify(genuine['declaration'],handle)==receipt


@pytest.mark.parametrize('claim',['none','new','copied','evidence','id','boolean'])
def test_unissued_or_copied_claim_never_approves(genuine,setup,claim):
    operator,verifier,issue,_=setup();handle=issue()
    # A fresh object has the same public class but no private ledger entry.
    receipt=verifier.verify(genuine['declaration'],handle)
    values={'none':None,'new':RelationshipApproval(),'copied':object.__new__(RelationshipApproval),
            'evidence':receipt, 'id':receipt.approval_id,'boolean':True}
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],values[claim])
    with pytest.raises(TypeError):copy(handle)


def test_no_approval_or_foreign_registry_defaults_deny(genuine,setup):
    _,verifier,_,_=setup();other,foreign,issue,_=setup();handle=issue()
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)
    assert foreign.verify(genuine['declaration'],handle).status=='RELATIONSHIP_ONLY'
    assert not hasattr(verifier,'approve')


@pytest.mark.parametrize('path,value',[
 (('capabilities','artifact_digest'),'f'*64), (('capabilities','candidate_bundle_id'),'f'*64),
 (('candidate','manifest_digest'),'f'*64), (('candidate','training_plan','plan_id'),'forged'),
 (('candidate','fitted_state_id'),'f'*64), (('candidate','training_schema_digest'),'f'*64),
 (('runtime','source_commit'),'f'*40), (('relationship','training_purpose'),'offline_forecast_evaluation'),
 (('relationship','evaluation_purpose'),'production_training'), (('relationship','metric'),'other'),
 (('relationship','unit'),'other'), (('relationship','calendar'),'iso_week'),
 (('relationship','timezone'),'Europe/Berlin'), (('relationship','currency'),'USD'),
 (('relationship','evaluation','reference','organization_id'),str(UUID(int=99))),
 (('relationship','evaluation','reference','dataset_id'),str(UUID(int=99))),
 (('relationship','evaluation','mapping_digest'),'f'*64),
 (('relationship','evaluation','series_ids'),[str(UUID(int=99))]),
 (('coverage','fold_id'),'wrong-fold'), (('coverage','periods',0,'horizon_step'),2),
 (('coverage','periods',0,'forecast_origin'),'2020-02-03T00:00:00Z'),
 (('coverage','periods',0,'target_period'),'2020-02-24T00:00:00Z'),
])
def test_exact_scope_substitution_denied(genuine,setup,path,value):
    _,verifier,issue,_=setup();handle=issue();data=genuine['declaration'].model_dump(mode='json');v=data
    for key in path[:-1]:v=v[key]
    v[path[-1]]=value
    # model_construct intentionally bypasses validation; the public B0 boundary
    # must revalidate. Use a root copy containing raw malformed nested values.
    bad=ModelEvaluationEligibility.model_construct(**data)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(bad,handle)


def test_coherent_metadata_rewrite_is_still_not_the_approved_record(genuine,setup):
    _,verifier,issue,_=setup();handle=issue();data=genuine['declaration'].model_dump()
    data['candidate']['manifest_digest']='f'*64
    structurally_valid=ModelEvaluationEligibility.model_validate(data)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(structurally_valid,handle)


@pytest.mark.parametrize('metadata', ['training','evaluation'])
@pytest.mark.parametrize('fault',['purpose','manifest','admission','reference','source'])
def test_metadata_cannot_be_forged_into_publication_evidence(genuine,setup,metadata,fault):
    _,_,issue,_=setup();original=genuine[metadata]
    changes={'purpose':dict(purpose='wrong'), 'manifest':dict(manifest_digest='f'*64),
             'admission':dict(admission_digest='f'*64), 'source':dict(source_id=UUID(int=99)),
             'reference':dict(reference=replace(original.reference,dataset_id=UUID(int=99)))}[fault]
    with pytest.raises(RelationshipAuthorityError):issue(**{metadata+'_metadata':replace(original,**changes)})


@pytest.mark.parametrize('provider_name',['training','evaluation'])
def test_live_publication_revocation_and_expired_admission(genuine,setup,monkeypatch,provider_name):
    _,verifier,issue,_=setup();handle=issue()
    provider=genuine['b']['provider'] if provider_name=='training' else genuine['evaluation_provider']
    monkeypatch.setattr(provider,'authorize_read',lambda *_:False)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


def test_admission_expiry_is_not_relationship_ttl(genuine,setup,monkeypatch):
    _,verifier,issue,_=setup();handle=issue();provider=genuine['evaluation_provider'];now=provider.clock()
    monkeypatch.setattr(provider,'clock',lambda:now+timedelta(days=3))
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


@pytest.mark.parametrize('state',['sealed','staging','aborted','rejected'])
def test_nonfinalized_publication_denies(genuine,setup,monkeypatch,state):
    _,verifier,issue,_=setup();handle=issue();store=genuine['evaluation_store'];original=store._candidate
    def candidate(*a,**kw):
        c=original(*a,**kw);c.state=state;return c
    monkeypatch.setattr(store,'_candidate',candidate)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


def test_revocation_is_permanent_and_stale_evidence_is_not_permission(genuine,setup):
    operator,verifier,issue,_=setup();handle=issue();old=verifier.verify(genuine['declaration'],handle)
    operator.revoke(handle);operator.revoke(handle)
    for claim in (handle,old):
        with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],claim)
    with pytest.raises(RelationshipAuthorityError):issue()


def test_expiry_exact_boundary_and_cumulative_admission(genuine,setup):
    _,verifier,issue,clock=setup(budgets=RelationshipAuthorityBudgets(max_verifications=1));handle=issue(valid_for_seconds=5)
    verifier.verify(genuine['declaration'],handle)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)
    _,verifier,issue,clock=setup();handle=issue(valid_for_seconds=5);clock[0]=5.
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


@pytest.mark.parametrize('budget',['max_total_record_bytes','max_declaration_bytes'])
def test_record_budgets_reject_before_publication_work(genuine,setup,monkeypatch,budget):
    _,_,issue,_=setup(budgets=RelationshipAuthorityBudgets(**{budget:1}))
    monkeypatch.setattr(genuine['evaluation_provider'],'require_current',lambda *a,**kw:pytest.fail('admit before verification'))
    with pytest.raises(RelationshipAuthorityError):issue()


@pytest.mark.parametrize('fault',['cancel','deadline','revoke','secret'])
def test_final_provider_callback_failure_returns_no_approval_evidence(genuine,setup,monkeypatch,fault):
    operator,verifier,issue,clock=setup();handle=issue();provider=genuine['evaluation_provider'];original=provider.require_current;stop=[False]
    def final(*a,**kw):
        result=original(*a,**kw)
        if fault=='cancel':stop[0]=True
        elif fault=='deadline':clock[0]=10.
        elif fault=='revoke':operator.revoke(handle)
        else:raise RuntimeError('CALLBACK_SECRET_728')
        return result
    monkeypatch.setattr(provider,'require_current',final)
    with pytest.raises(RelationshipAuthorityError) as e:verifier.verify(genuine['declaration'],handle,cancelled=lambda:stop[0])
    assert 'CALLBACK_SECRET_728' not in ''.join(traceback.format_exception(e.value))
    assert e.value.__context__ is None and e.value.__cause__ is None


def test_relationship_grants_neither_d2_ownership_nor_model_use(genuine,setup,monkeypatch):
    _,verifier,issue,_=setup();handle=issue();receipt=verifier.verify(genuine['declaration'],handle)
    b=genuine['b'];clone=CandidateBundle(bytes(bytearray(genuine['bundle'].payload)))
    with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(clone)
    for obj in (handle,receipt,verifier):
        assert not any(hasattr(obj,name) for name in ('manifest','payload','artifact','booster','predict','load','labels','matrix','state_json'))
    assert genuine['declaration'].status=='DECLARATION_ONLY'
    assert genuine['declaration'].authorization.issuance=='NOT_IMPLEMENTED'
    import xgboost as xgb
    monkeypatch.setattr(xgb.Booster,'load_model',lambda *a,**k:pytest.fail('B0 cannot load models'))
    for wrong in (handle,receipt):
        with pytest.raises(CandidateTrainingError):b['owner'].verify_candidate(wrong)
    assert verifier.verify(genuine['declaration'],handle)==receipt


@pytest.mark.parametrize('entry',['verify','approve','revoke','bootstrap'])
def test_public_errors_do_not_leak_secret_names_or_context(genuine,setup,capsys,caplog,entry):
    operator,verifier,issue,_=setup();handle=issue();secret='B0_'+uuid4().hex
    bad=genuine['declaration'].model_copy(update={secret:secret})
    operations={'verify':lambda:verifier.verify(bad,handle),
        'approve':lambda:operator.approve(bad,training_metadata=genuine['training'],evaluation_metadata=genuine['evaluation'],approval_reference='decision-17'),
        'revoke':lambda:operator.revoke(**{secret:secret}),
        'bootstrap':lambda:bootstrap_relationship_authority(**{secret:secret})}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        try:raise ValueError(secret)
        except ValueError:
            with pytest.raises(RelationshipAuthorityError) as error:operations[entry]()
    e=error.value;out=capsys.readouterr()
    assert not seen and not out.err and e.__context__ is None and e.__cause__ is None
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.out+out.err+caplog.text
    with warnings.catch_warnings(record=True) as other:
        warnings.simplefilter('always');warnings.warn('unrelated-b0-warning',UserWarning)
    assert len(other)==1


@pytest.mark.parametrize('change',['candidate','artifact','invocation','relationship','origins'])
def test_structurally_valid_substitution_cannot_reuse_approval(genuine,setup,change):
    _,verifier,issue,_=setup();handle=issue();data=genuine['declaration'].model_dump()
    if change in ('candidate','artifact'):
        field='candidate_bundle_id' if change=='candidate' else 'artifact_digest'
        data['candidate'][field]='e'*64;data['capabilities'][field]='e'*64
    elif change=='invocation':
        from src.data.contracts.model_candidate import digest,TrainingInvocation
        data['candidate']['training_invocation']['invocation_id']='another-approved-looking-invocation'
        inv=TrainingInvocation.model_validate(data['candidate']['training_invocation'])
        data['candidate']['training_invocation_digest']=digest(inv.model_dump_json().encode())
    elif change=='relationship':data['relationship']['relationship_id']='different-operator-decision'
    else:
        # Geometry remains H1, but date now belongs to training and must reject
        # even if an unchecked constructed root is supplied.
        data['coverage']['periods'][0]['forecast_origin']=data['candidate']['training_plan']['folds'][0]['training_origins'][0]
        with pytest.raises(RelationshipAuthorityError):verifier.verify(ModelEvaluationEligibility.model_construct(**data),handle)
        return
    forged=ModelEvaluationEligibility.model_validate(data)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(forged,handle)


@pytest.mark.parametrize('scope',['root','candidate','capabilities','relationship','coverage','runtime','resources','authorization'])
def test_unknown_nested_secret_is_sanitized_at_request_boundary(genuine,setup,scope,capsys,caplog):
    _,verifier,issue,_=setup();handle=issue();data=genuine['declaration'].model_dump(mode='json');secret='UNK_'+uuid4().hex
    target=data if scope=='root' else data[scope];target[secret]={'value':secret}
    # model_construct discards root extras before ingress; an unchecked copy
    # retains them and challenges the real revalidation boundary.
    bad=(genuine['declaration'].model_copy(update={secret:secret}) if scope=='root'
         else ModelEvaluationEligibility.model_construct(**data))
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(RelationshipAuthorityError) as e:verifier.verify(bad,handle)
    out=capsys.readouterr()
    assert not seen and secret not in str(e.value)+repr(e.value)+repr(e.value.args)+''.join(traceback.format_exception(e.value))+out.err+out.out+caplog.text
    assert e.value.__context__ is None and e.value.__cause__ is None


def test_capacity_is_cumulative_and_cannot_be_freed_by_revocation(genuine,setup,monkeypatch):
    operator,_,issue,_=setup(budgets=RelationshipAuthorityBudgets(max_records=1));handle=issue();operator.revoke(handle)
    data=genuine['declaration'].model_dump();data['relationship']['relationship_id']='new-decision'
    another=ModelEvaluationEligibility.model_validate(data)
    monkeypatch.setattr(genuine['evaluation_provider'],'require_current',lambda *a,**k:pytest.fail('capacity must precede live work'))
    with pytest.raises(RelationshipAuthorityError):operator.approve(another,training_metadata=genuine['training'],
        evaluation_metadata=genuine['evaluation'],approval_reference='decision-17')


def test_final_cancellation_callback_can_advance_deadline_or_revoke(genuine,setup):
    for change in ('deadline','revoke'):
        operator,verifier,issue,clock=setup();handle=issue();calls=[0]
        def callback():
            calls[0]+=1
            if calls[0]==6:
                if change=='deadline':clock[0]=10.
                else:operator.revoke(handle)
            return False
        with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle,cancelled=callback)
        assert calls[0]==6


def test_operator_authority_and_reference_must_match_explicit_record(genuine,setup):
    for args in ({'authority_id':'another-ledger'},{'authority_version':'2.0'}):
        _,_,issue,_=setup(**args)
        with pytest.raises(RelationshipAuthorityError):issue()
    _,_,issue,_=setup()
    with pytest.raises(RelationshipAuthorityError):issue(approval_reference='unlisted-record')


@pytest.mark.parametrize('limits',[
 {'max_records':True},{'max_records':17},{'max_total_record_bytes':0},
 {'max_elapsed_seconds':11},{'max_verifications':257},{'max_validity_seconds':3601}])
def test_bootstrap_limits_cannot_be_disabled(genuine,setup,limits):
    with pytest.raises(RelationshipAuthorityError):setup(budgets=RelationshipAuthorityBudgets(**limits))


@pytest.mark.parametrize('facade',['RelationshipApprovalOperator','RelationshipApprovalVerifier'])
def test_caller_provided_fake_registry_is_not_a_trusted_facade(facade):
    import src.services.training_evaluation_relationship_authority as module
    class FakeRegistry:
        def verify(self,*a,**kw):return True
        def approve(self,*a,**kw):return RelationshipApproval()
    with pytest.raises(RelationshipAuthorityError):getattr(module,facade)(FakeRegistry())


def test_public_receipt_never_aliases_private_expiry_or_provenance(genuine,setup):
    _,verifier,issue,clock=setup();handle=issue(valid_for_seconds=5)
    first=verifier.verify(genuine['declaration'],handle);second=verifier.verify(genuine['declaration'],handle)
    assert first==second and first is not second and not hasattr(first,'__dict__')
    # Deliberately bypass Python frozen assignment: even this cannot mutate the
    # owner's private record or prolong the approval's lifetime.
    object.__setattr__(first,'expires_at_monotonic',99999.)
    assert verifier.verify(genuine['declaration'],handle).expires_at_monotonic==5.
    clock[0]=5.
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


def test_bootstrap_budget_alias_cannot_disable_limits(genuine,setup):
    limits=RelationshipAuthorityBudgets(max_verifications=1)
    _,verifier,issue,_=setup(budgets=limits);handle=issue()
    object.__setattr__(limits,'max_verifications',256)
    verifier.verify(genuine['declaration'],handle)
    with pytest.raises(RelationshipAuthorityError):verifier.verify(genuine['declaration'],handle)


def test_issued_record_snapshots_operator_metadata_and_nested_reference(genuine,setup,monkeypatch):
    _,verifier,issue,_=setup();handle=issue()
    detached=ModelEvaluationEligibility.model_validate_json(genuine['declaration'].model_dump_json())
    # Simulate mutation of bootstrap input aliases; the original approval must
    # retain the authenticated metadata/reference snapshot, not these objects.
    metadata=genuine['training']
    monkeypatch.setitem(metadata.__dict__,'rows',99999)
    monkeypatch.setitem(metadata.reference.__dict__,'dataset_id',UUID(int=999))
    assert verifier.verify(detached,handle).eligibility_id==detached.eligibility_id
