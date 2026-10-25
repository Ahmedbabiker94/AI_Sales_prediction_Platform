"""M9 process-local decision declarations; no record is model-use authority."""
from typing import Literal
from uuid import UUID
from pydantic import Field,StrictInt,StrictStr,model_validator
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_package import Digest
from src.data.contracts.candidate_evaluation import CandidateEvidence
from src.data.contracts.model_representation import RepresentationContract,RepresentationTimestamp

State=Literal['CANDIDATE_REGISTERED','EVIDENCE_VERIFIED','REVIEW_PENDING','APPROVED_FOR_FURTHER_VALIDATION','REJECTED','REVOKED']
Action=Literal['REGISTER','VERIFY_EVIDENCE','REQUEST_REVIEW','APPROVE','REJECT','REVOKE']
Role=Literal['administrator','approver','reviewer']
Reason=Literal['REGISTER_CANDIDATE','VERIFIED_TEST_ONLY_EVIDENCE','REQUEST_OFFLINE_REVIEW',
    'ALLOW_FURTHER_VALIDATION_ONLY','OPERATOR_REJECTION','EVIDENCE_INVALIDATED','AUTHORITY_WITHDRAWN']
TRANSITIONS={
 'VERIFY_EVIDENCE':('CANDIDATE_REGISTERED','EVIDENCE_VERIFIED','reviewer',('VERIFIED_TEST_ONLY_EVIDENCE',)),
 'REQUEST_REVIEW':('EVIDENCE_VERIFIED','REVIEW_PENDING','reviewer',('REQUEST_OFFLINE_REVIEW',)),
 'APPROVE':('REVIEW_PENDING','APPROVED_FOR_FURTHER_VALIDATION','approver',('ALLOW_FURTHER_VALIDATION_ONLY',)),
}
ZERO='0'*64


class AuthorityContext(RepresentationContract):
    version:Literal['1.0']='1.0'
    authority_id:StrictStr=Field(min_length=1,max_length=128)
    bootstrap_context:Digest
    identity_kind:Literal['TRUSTED_HOST_ASSERTION']='TRUSTED_HOST_ASSERTION'


class LifecyclePolicy(RepresentationContract):
    version:Literal['1.0']='1.0'
    authority:AuthorityContext
    organization_id:UUID
    dataset_id:UUID
    valid_from:RepresentationTimestamp
    expires_at:RepresentationTimestamp
    assessment_cutoff:RepresentationTimestamp
    evidence_max_age_seconds:StrictInt=Field(default=300,ge=1,le=3600)
    max_lifecycles:StrictInt=Field(default=32,ge=1,le=64)
    max_transitions:StrictInt=Field(default=256,ge=1,le=512)
    max_evidence:StrictInt=Field(default=16,ge=1,le=32)
    max_evidence_bytes:StrictInt=Field(default=2*1024*1024,ge=1,le=4*1024*1024)
    selection_assurance:Literal['LOCAL_M9_ORDER_ONLY']='LOCAL_M9_ORDER_ONLY'
    purpose:Literal['CONTROLLED_OFFLINE_H1_FURTHER_VALIDATION_ONLY']='CONTROLLED_OFFLINE_H1_FURTHER_VALIDATION_ONLY'
    evidence:Literal['TEST_ONLY_NON_DEPLOYABLE']='TEST_ONLY_NON_DEPLOYABLE'

    @model_validator(mode='after')
    def times(self):
        if not self.valid_from<self.expires_at or self.assessment_cutoff>self.valid_from:raise ValueError('explicit policy interval required')
        return self
    @property
    def policy_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class ActorAssertion(RepresentationContract):
    version:Literal['1.0']='1.0'
    actor_id:StrictStr=Field(min_length=1,max_length=128)
    identity_context:Digest
    roles:tuple[Role,...]=Field(min_length=1,max_length=3)
    candidate_ids:tuple[Digest,...]=Field(min_length=1,max_length=8)
    valid_from:RepresentationTimestamp
    expires_at:RepresentationTimestamp

    @model_validator(mode='after')
    def explicit(self):
        if (self.roles!=tuple(sorted(set(self.roles))) or self.candidate_ids!=tuple(sorted(set(self.candidate_ids)))
                or not self.valid_from<self.expires_at):raise ValueError('exact finite actor scope required')
        return self


class ActorGrant(RepresentationContract):
    version:Literal['1.0']='1.0'
    authority:AuthorityContext
    policy_id:Digest
    assertion:ActorAssertion
    @property
    def grant_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class AuthorityEvent(RepresentationContract):
    version:Literal['1.0']='1.0'
    index:StrictInt=Field(ge=1,le=128)
    previous:Digest
    action:Literal['GRANTED','REVOKED']
    grant:ActorGrant
    occurred_at:RepresentationTimestamp
    @property
    def event_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class RegistrationRequest(RepresentationContract):
    version:Literal['1.0']='1.0'
    request_id:Digest
    candidate_id:Digest
    policy_id:Digest
    previous_lifecycle_id:Digest|None=None
    @property
    def digest(self):return identity(self.model_dump(mode='json',warnings='error'))


class DecisionRequest(RepresentationContract):
    version:Literal['1.0']='1.0'
    request_id:Digest
    lifecycle_id:Digest
    expected_version:StrictInt=Field(ge=1,le=512)
    policy_id:Digest
    action:Literal['VERIFY_EVIDENCE','REQUEST_REVIEW','APPROVE','REJECT','REVOKE']
    reason:Reason
    evidence_id:Digest|None=None
    selection_claim:Literal['NO_GLOBAL_PRETEST_GUARANTEE']='NO_GLOBAL_PRETEST_GUARANTEE'
    @property
    def digest(self):return identity(self.model_dump(mode='json',warnings='error'))


class EvidenceReference(RepresentationContract):
    version:Literal['1.0']='1.0'
    local_sequence:StrictInt=Field(ge=1,le=1024)
    lifecycle_id:Digest
    actor_grant_id:Digest
    request_id:Digest
    report_id:Digest
    ensemble_evaluation_id:Digest
    configuration_id:Digest
    population_id:Digest
    truth_revision_id:Digest
    publication_id:Digest
    source_ids:tuple[Digest,...]=Field(min_length=2,max_length=8)
    partition:Literal['validation','test']
    assessment_cutoff:RepresentationTimestamp
    observed_at:RepresentationTimestamp
    expires_at:RepresentationTimestamp
    precommit_id:Digest|None=None
    classification:Literal['TEST_ONLY_NON_DEPLOYABLE']='TEST_ONLY_NON_DEPLOYABLE'
    @model_validator(mode='after')
    def bounded(self):
        if (self.source_ids!=tuple(sorted(set(self.source_ids))) or not self.observed_at<self.expires_at
                or self.partition=='test' and self.precommit_id is None):raise ValueError('exact evidence context required')
        return self
    @property
    def evidence_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class PrecommitRecord(RepresentationContract):
    version:Literal['1.0']='1.0'
    local_sequence:StrictInt=Field(ge=1,le=1024)
    policy_id:Digest
    lifecycle_id:Digest
    actor_grant_id:Digest
    validation_evidence_id:Digest
    test_request_id:Digest
    configuration_id:Digest
    scope_id:Digest
    audit_head_at_commit:Digest
    observed_at:RepresentationTimestamp
    expires_at:RepresentationTimestamp
    assurance:Literal['LOCAL_M9_ORDER_ONLY']='LOCAL_M9_ORDER_ONLY'
    @model_validator(mode='after')
    def times(self):
        if not self.observed_at<self.expires_at:raise ValueError('finite precommit interval required')
        return self
    @property
    def precommit_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class LocalDisclosureRecord(RepresentationContract):
    version:Literal['1.0']='1.0'
    local_sequence:StrictInt=Field(ge=1,le=1024)
    policy_id:Digest
    lifecycle_id:Digest
    actor_grant_id:Digest
    precommit_id:Digest
    request_id:Digest
    candidate_ids:tuple[Digest,...]=Field(min_length=2,max_length=8)
    observed_at:RepresentationTimestamp
    status:Literal['ATTEMPT_MAY_DISCLOSE_TEST_TRUTH']='ATTEMPT_MAY_DISCLOSE_TEST_TRUTH'
    @property
    def disclosure_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class LifecycleState(RepresentationContract):
    version:Literal['1.0']='1.0'
    lifecycle_id:Digest
    candidate:CandidateEvidence
    policy_id:Digest
    state:State
    state_version:StrictInt=Field(ge=1,le=512)
    head_id:Digest
    evidence_id:Digest|None=None
    previous_lifecycle_id:Digest|None=None
    purpose:Literal['CONTROLLED_OFFLINE_FURTHER_VALIDATION_DECISION_ONLY']='CONTROLLED_OFFLINE_FURTHER_VALIDATION_DECISION_ONLY'


class TransitionRecord(RepresentationContract):
    version:Literal['1.0']='1.0'
    index:StrictInt=Field(ge=1,le=512)
    previous_audit_id:Digest
    lifecycle_id:Digest
    candidate:CandidateEvidence
    policy_id:Digest
    actor:ActorGrant
    request_id:Digest
    request_digest:Digest
    before:State|None
    after:State
    version_before:StrictInt=Field(ge=0,le=511)
    version_after:StrictInt=Field(ge=1,le=512)
    action:Action
    reason:Reason
    occurred_at:RepresentationTimestamp
    evidence:EvidenceReference|None=None
    previous_lifecycle_id:Digest|None=None
    @model_validator(mode='after')
    def valid(self):
        if (self.version_after!=self.version_before+1 or self.actor.policy_id!=self.policy_id
                or self.candidate.candidate_id not in self.actor.assertion.candidate_ids
                or not self.actor.assertion.valid_from<=self.occurred_at<self.actor.assertion.expires_at):raise ValueError('exact policy/version/actor required')
        if self.action=='REGISTER':
            if (self.before is not None or self.after!='CANDIDATE_REGISTERED' or self.version_before!=0
                    or self.reason!='REGISTER_CANDIDATE' or self.evidence is not None
                    or not {'reviewer','administrator'}&set(self.actor.assertion.roles)):raise ValueError()
        elif self.action in TRANSITIONS:
            before,after,role,reasons=TRANSITIONS[self.action]
            if (self.before,self.after)!=(before,after) or self.reason not in reasons or self.evidence is None:raise ValueError()
            if role not in self.actor.assertion.roles or not set(self.evidence.source_ids)<=set(self.actor.assertion.candidate_ids):raise ValueError()
        else:
            if self.before in (None,'REJECTED','REVOKED') or 'administrator' not in self.actor.assertion.roles:raise ValueError()
            if self.action=='REJECT':
                if self.after!='REJECTED' or self.before=='APPROVED_FOR_FURTHER_VALIDATION' or self.reason!='OPERATOR_REJECTION':raise ValueError()
            elif self.after!='REVOKED' or self.reason not in ('EVIDENCE_INVALIDATED','AUTHORITY_WITHDRAWN'):raise ValueError()
        if self.evidence is not None and self.evidence.lifecycle_id!=self.lifecycle_id:raise ValueError()
        return self
    @property
    def transition_id(self):return identity(self.model_dump(mode='json',warnings='error'))


class DecisionOutcome(RepresentationContract):
    version:Literal['1.0']='1.0'
    status:Literal['APPLIED','IDEMPOTENT']
    transition:TransitionRecord
    current:LifecycleState
    @model_validator(mode='after')
    def bound(self):
        if self.transition.lifecycle_id!=self.current.lifecycle_id or self.transition.version_after>self.current.state_version:raise ValueError()
        return self


class LifecycleError(ValueError):pass


def reject_lifecycle():
    error=LifecycleError('candidate lifecycle request rejected')
    try:raise error from None
    finally:error.__cause__=None;error.__context__=None
