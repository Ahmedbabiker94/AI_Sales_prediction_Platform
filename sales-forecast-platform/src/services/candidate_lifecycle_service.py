"""M9 original-owner orchestration and explicit local decision policy.

The host protects bootstrap/operator, original M8 composition and clock. There
is no independent IAM, native model-use authority, durable approval or global
pre-test disclosure guarantee. Evidence freshness re-evaluates the EXACT frozen
M8 request (authorized label reads, never native operations).
"""
from dataclasses import dataclass,replace
from datetime import datetime,timedelta,timezone
from threading import RLock
from src.data.contracts.backtesting import identity
from src.data.contracts.dataset import operational_json
from src.data.contracts.candidate_evaluation import CandidateEvaluationRequest,MultiCandidateComparison
from src.data.contracts.candidate_lifecycle import (
 AuthorityContext,LifecyclePolicy,ActorAssertion,ActorGrant,AuthorityEvent,RegistrationRequest,DecisionRequest,
 EvidenceReference,PrecommitRecord,LocalDisclosureRecord,TransitionRecord,TRANSITIONS,ZERO,reject_lifecycle)
from src.services.candidate_evaluation_service import CandidateEvaluationService,evidence
from src.repositories.candidate_lifecycle_repository import CandidateLifecycleRepository
from src.providers.candidate_lifecycle_provider import CandidateLifecycleProvider


class _Capability:
    __slots__=()
    def __reduce__(self):raise TypeError('process-local capability cannot be serialized')
class ActorCapability(_Capability):__slots__=()
class LifecycleHandle(_Capability):__slots__=()
class EvidenceHandle(_Capability):__slots__=()
class PrecommitHandle(_Capability):__slots__=()


@dataclass(frozen=True)
class _Actor:
    handle:object
    grant:ActorGrant
    revoked:bool=False
@dataclass(frozen=True)
class _Evidence:
    handle:object
    reference:EvidenceReference
    request:CandidateEvaluationRequest
    report:MultiCandidateComparison
@dataclass(frozen=True)
class _Precommit:
    handle:object
    record:PrecommitRecord
    request:CandidateEvaluationRequest
    consumed:bool=False


def _parse(cls,value):
    if type(value) is cls:return cls.model_validate_json(value.canonical_json)
    if type(value) is dict:return cls.model_validate_json(operational_json(value))
    if type(value) in (str,bytes):return cls.model_validate_json(value)
    raise ValueError()


class _Root:
    def __init__(self,evaluator,policy,clock):
        if type(evaluator) is not CandidateEvaluationService or evaluator._self is not evaluator or not callable(clock):raise ValueError()
        self.policy=_parse(LifecyclePolicy,policy);self.evaluator=evaluator;self.clock=clock
        self.lock=RLock();self.busy=False;self.last_time=None
        self.provider=CandidateLifecycleProvider(CandidateLifecycleRepository())
        self.actors={};self.authority_events=();self.lives={};self.evidences={};self.precommits={};self.disclosed=set()
        self.operator=None;self.service=None;self.sources={};self.local_sequence=0;self.disclosures=()
        for cid,binding in evaluator._bindings.items():
            manifest=binding.packaging.verify(binding.package_id,candidate=binding.candidate)
            ds=manifest.plan.datasets[0].reference
            if ((ds.organization_id,ds.dataset_id)!=(self.policy.organization_id,self.policy.dataset_id)
                    or manifest.plan.folds[0].assessment_cutoff!=self.policy.assessment_cutoff):raise ValueError()
            self.sources[cid]=evidence(manifest)
        self._now()

    def _next(self):
        if self.local_sequence>=1024:raise ValueError()
        self.local_sequence+=1;return self.local_sequence
    def _now(self):
        now=self.clock()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:raise ValueError()
        now=now.astimezone(timezone.utc)
        if self.last_time is not None and now<self.last_time:raise ValueError()
        self.last_time=now;return now
    def _policy_current(self):
        now=self._now()
        if not self.policy.valid_from<=now<self.policy.expires_at:raise ValueError()
        return now
    def _actor_at(self,handle,candidates,role,now):
        record=self.actors.get(id(handle))
        if (type(handle) is not ActorCapability or record is None or record.handle is not handle or record.revoked
                or not self.policy.valid_from<=now<self.policy.expires_at
                or not record.grant.assertion.valid_from<=now<record.grant.assertion.expires_at
                or not set(candidates)<=set(record.grant.assertion.candidate_ids)
                or role is not None and role not in record.grant.assertion.roles):raise ValueError()
        return record.grant
    def _actor(self,handle,candidates,role=None):
        return self._actor_at(handle,candidates,role,self._policy_current())
    def _grant_at(self,grant_id,candidates,now):
        found=[r for r in self.actors.values() if r.grant.grant_id==grant_id]
        if len(found)!=1:raise ValueError()
        return self._actor_at(found[0].handle,candidates,None,now)
    def _grant_by_id(self,grant_id,candidates):
        return self._grant_at(grant_id,candidates,self._policy_current())
    def _evidence_record(self,handle,life_id):
        record=self.evidences.get(id(handle))
        if type(handle) is not EvidenceHandle or record is None or record.handle is not handle or record.reference.lifecycle_id!=life_id:raise ValueError()
        return record
    def _precommit_current(self,p,now):
        ids=self._members(p.request)
        if not p.record.observed_at<=now<p.record.expires_at:raise ValueError()
        self._grant_at(p.record.actor_grant_id,ids,now)
        validation=[e for e in self.evidences.values() if e.reference.evidence_id==p.record.validation_evidence_id]
        if len(validation)!=1:raise ValueError()
        self._evidence_current(validation[0],now)
        if (validation[0].reference.local_sequence>=p.record.local_sequence
                or self.disclosures and p.record.local_sequence>=min(d.local_sequence for d in self.disclosures)):raise ValueError()
    def _evidence_current(self,record,now):
        ref=record.reference;ids=self._members(record.request)
        self._grant_at(ref.actor_grant_id,ids,now)
        if not ref.observed_at<=now<ref.expires_at:raise ValueError()
        if ref.precommit_id is not None:
            pre=[p for p in self.precommits.values() if p.record.precommit_id==ref.precommit_id]
            if len(pre)!=1 or not pre[0].consumed:raise ValueError()
            self._precommit_current(pre[0],now)
            disclosures=[d for d in self.disclosures if d.precommit_id==ref.precommit_id]
            if (len(disclosures)!=1 or disclosures[0].request_id!=ref.request_id
                    or not pre[0].record.local_sequence<disclosures[0].local_sequence<ref.local_sequence):raise ValueError()
    def _life(self,handle):
        entry=self.lives.get(id(handle))
        if type(handle) is not LifecycleHandle or entry is None or entry[0] is not handle:raise ValueError()
        state=self.provider._state(entry[1])
        if state is None:raise ValueError()
        return state
    def _source(self,cid):
        binding=self.evaluator._bindings.get(cid)
        if binding is None:raise ValueError()
        manifest=binding.packaging.verify(binding.package_id,candidate=binding.candidate)
        current=evidence(manifest)
        if current!=self.sources[cid]:raise ValueError()
        return current
    def _request(self,value):
        req=_parse(CandidateEvaluationRequest,value)
        if req.plan.folds[0].assessment_cutoff!=self.policy.assessment_cutoff:raise ValueError()
        return req
    def _members(self,req):return tuple(b.source.candidate_id for b in req.batches)
    def _scope(self,req):
        return identity([req.plan.digest,req.evaluation.model_dump(mode='json'),[k.key_id for k in req.keys],self._members(req)])

    def _grant(self,value):
        assertion=_parse(ActorAssertion,value);self._policy_current()
        if (not set(assertion.candidate_ids)<=set(self.sources) or assertion.valid_from<self.policy.valid_from
                or assertion.expires_at>self.policy.expires_at or not assertion.valid_from<=self._now()<assertion.expires_at
                or len(self.actors)>=16):raise ValueError()
        grant=ActorGrant(authority=self.policy.authority,policy_id=self.policy.policy_id,assertion=assertion)
        if any(r.grant.grant_id==grant.grant_id for r in self.actors.values()):raise ValueError()
        handle=ActorCapability();self._authority_event('GRANTED',grant)
        self.actors[id(handle)]=_Actor(handle,grant);return handle
    def _authority_event(self,action,grant):
        previous=self.authority_events[-1].event_id if self.authority_events else ZERO
        event=AuthorityEvent(index=len(self.authority_events)+1,previous=previous,action=action,grant=grant,occurred_at=self.last_time)
        self.authority_events=self.authority_events+(event,)
    def _revoke_actor(self,handle):
        record=self.actors.get(id(handle))
        if type(handle) is not ActorCapability or record is None or record.handle is not handle or record.revoked:raise ValueError()
        self._now();self._authority_event('REVOKED',record.grant);self.actors[id(handle)]=replace(record,revoked=True)

    def _register(self,actor,value):
        req=_parse(RegistrationRequest,value)
        grant=self._actor(actor,(req.candidate_id,))
        if req.policy_id!=self.policy.policy_id or not {'administrator','reviewer'}&set(grant.assertion.roles):raise ValueError()
        source=self._source(req.candidate_id)
        life_id=identity(['m9-lifecycle-v1',req.digest,grant.grant_id])
        prior=self.provider._prior(life_id,req.request_id)
        if prior is None:
            latest=self.provider._latest(req.candidate_id)
            if latest!=req.previous_lifecycle_id:raise ValueError()
            if latest is not None:
                if 'administrator' not in grant.assertion.roles or self.provider._state(latest).state not in ('REJECTED','REVOKED'):raise ValueError()
        grant=self._actor(actor,(req.candidate_id,))
        record=TransitionRecord(index=self.provider._index(),previous_audit_id=self.provider._head(),lifecycle_id=life_id,
            candidate=source,policy_id=req.policy_id,actor=grant,request_id=req.request_id,request_digest=req.digest,
            before=None,after='CANDIDATE_REGISTERED',version_before=0,version_after=1,action='REGISTER',reason='REGISTER_CANDIDATE',
            occurred_at=self.last_time,previous_lifecycle_id=req.previous_lifecycle_id)
        fingerprint=identity([req.digest,grant.grant_id])
        outcome=self.provider._commit(record,fingerprint,self.policy)
        entries=[entry for entry in self.lives.values() if entry[1]==life_id]
        if entries:return entries[0][0],outcome
        handle=LifecycleHandle();self.lives[id(handle)]=(handle,life_id);return handle,outcome

    def _check_evidence(self,handle,life_id):
        record=self._evidence_record(handle,life_id)
        self._evidence_current(record,self._policy_current())
        # Re-evaluation refreshes canonical publication, value-revision and M7
        # authority through original M8. No native operation is reachable.
        fresh=self.evaluator.evaluate(record.request)
        if fresh.canonical_bytes!=record.report.canonical_bytes or fresh.report_id!=record.reference.report_id:raise ValueError()
        self._evidence_current(record,self._policy_current())
        return record

    def _precommit(self,actor,life,validation_handle,test_value):
        state=self._life(life)
        if state.state in ('REJECTED','REVOKED'):raise ValueError()
        req=self._request(test_value);ids=self._members(req)
        grant=self._actor(actor,ids,'reviewer')
        validation=self._evidence_record(validation_handle,state.lifecycle_id)
        if (validation.reference.partition!='validation' or req.partition!='test'
                or req.ensemble!=validation.request.ensemble or tuple(b.source for b in req.batches)!=tuple(b.source for b in validation.request.batches)
                or req.plan!=validation.request.plan or state.candidate.candidate_id not in ids
                or self.disclosed):raise ValueError()
        self._actor(actor,validation.reference.source_ids,'reviewer')
        validation=self._check_evidence(validation_handle,state.lifecycle_id)
        engine,_=self.evaluator._owned(req,lambda:None);self.evaluator._admit(req,engine)
        scope=self._scope(req)
        existing=[p for p in self.precommits.values() if p.record.scope_id==scope]
        if existing:
            p=existing[0]
            if (p.request!=req or p.record.actor_grant_id!=grant.grant_id or p.record.lifecycle_id!=state.lifecycle_id
                    or p.record.validation_evidence_id!=validation.reference.evidence_id or p.consumed):raise ValueError()
            self._precommit_current(p,self._policy_current())
            return p.handle
        if len(self.precommits)>=16:raise ValueError()
        self._actor(actor,ids,'reviewer');now=self.last_time
        self._evidence_current(validation,now)
        p=PrecommitRecord(local_sequence=self._next(),policy_id=self.policy.policy_id,lifecycle_id=state.lifecycle_id,actor_grant_id=grant.grant_id,
            validation_evidence_id=validation.reference.evidence_id,test_request_id=req.request_id,configuration_id=req.ensemble.configuration_id,
            scope_id=scope,audit_head_at_commit=self.provider._head(),observed_at=now,
            expires_at=min(now+timedelta(seconds=self.policy.evidence_max_age_seconds),self.policy.expires_at,grant.assertion.expires_at,validation.reference.expires_at))
        handle=PrecommitHandle();self.precommits[id(handle)]=_Precommit(handle,p,req);return handle

    def _capture(self,actor,life,value,precommit=None):
        state=self._life(life);req=self._request(value);ids=self._members(req)
        grant=self._actor(actor,ids,'reviewer')
        if state.state in ('REJECTED','REVOKED') or state.candidate.candidate_id not in ids or len(self.evidences)>=self.policy.max_evidence:raise ValueError()
        pre_id=None
        if req.partition=='test':
            p=self.precommits.get(id(precommit))
            if (type(precommit) is not PrecommitHandle or p is None or p.handle is not precommit or p.consumed
                    or p.request!=req or p.record.lifecycle_id!=state.lifecycle_id or self._now()>=p.record.expires_at):raise ValueError()
            self._precommit_current(p,self._policy_current())
            # Mark before any M8 call: a failed call can have disclosed truth.
            disclosure=LocalDisclosureRecord(local_sequence=self._next(),policy_id=self.policy.policy_id,
                lifecycle_id=state.lifecycle_id,actor_grant_id=grant.grant_id,precommit_id=p.record.precommit_id,
                request_id=req.request_id,candidate_ids=ids,observed_at=self.last_time)
            self.precommits[id(precommit)]=replace(p,consumed=True)
            self.disclosures=self.disclosures+(disclosure,);self.disclosed.update(ids);pre_id=p.record.precommit_id
        elif precommit is not None:raise ValueError()
        report=self.evaluator.evaluate(req)
        if (report.request_id!=req.request_id or report.sources!=tuple(b.source for b in req.batches)
                or report.ensemble.configuration!=req.ensemble or len(report.canonical_bytes)>self.policy.max_evidence_bytes):raise ValueError()
        self._actor(actor,ids,'reviewer');now=self.last_time
        if pre_id is not None:self._precommit_current(self.precommits[id(precommit)],now)
        ref=EvidenceReference(local_sequence=self._next(),lifecycle_id=state.lifecycle_id,actor_grant_id=grant.grant_id,request_id=req.request_id,
            report_id=report.report_id,ensemble_evaluation_id=report.ensemble.ensemble_evaluation_id,
            configuration_id=report.ensemble.configuration.configuration_id,population_id=report.population_id,
            truth_revision_id=report.truth_revision_id,publication_id=identity(report.evaluation.model_dump(mode='json')),
            source_ids=ids,partition=req.partition,assessment_cutoff=req.plan.folds[0].assessment_cutoff,observed_at=now,
            expires_at=min(now+timedelta(seconds=self.policy.evidence_max_age_seconds),self.policy.expires_at,grant.assertion.expires_at,self.precommits[id(precommit)].record.expires_at if pre_id else self.policy.expires_at),precommit_id=pre_id)
        handle=EvidenceHandle();self.evidences[id(handle)]=_Evidence(handle,ref,req,report);return handle,ref

    def _decide(self,actor,life,value,evidence_handle=None):
        req=_parse(DecisionRequest,value);state=self._life(life);cid=state.candidate.candidate_id
        if req.lifecycle_id!=state.lifecycle_id or req.policy_id!=self.policy.policy_id:raise ValueError()
        role='administrator' if req.action in ('REJECT','REVOKE') else TRANSITIONS[req.action][2]
        grant=self._actor(actor,(cid,),role);ref=None
        prior=self.provider._prior(state.lifecycle_id,req.request_id)
        intent=identity([req.digest,grant.grant_id,req.evidence_id])
        if prior is not None and prior[0]!=intent:raise ValueError()
        if prior is None:
            if self.provider._index()>self.policy.max_transitions:raise ValueError()
            if req.expected_version!=state.state_version:raise ValueError()
            if req.action in TRANSITIONS:
                before,_,_,reasons=TRANSITIONS[req.action]
                if state.state!=before or req.reason not in reasons:raise ValueError()
            elif state.state in ('REJECTED','REVOKED'):raise ValueError()
        if req.action in TRANSITIONS:
            e=self._evidence_record(evidence_handle,state.lifecycle_id);ref=e.reference
            if req.evidence_id!=ref.evidence_id:raise ValueError()
            self._actor(actor,ref.source_ids,role)
            e=self._check_evidence(evidence_handle,state.lifecycle_id);self._source(cid)
        elif req.evidence_id is not None or evidence_handle is not None:raise ValueError()
        # Restrictive administrator transitions do not assert that stale/corrupt
        # evidence is valid: they remain possible precisely to withdraw decisions.
        fingerprint=identity([req.digest,grant.grant_id,ref.evidence_id if ref else None])
        prior=self.provider._prior(state.lifecycle_id,req.request_id)
        if prior is not None:
            if prior[0]!=fingerprint:raise ValueError()
            self._actor(actor,ref.source_ids if ref else (cid,),role)
            if ref is not None:self._evidence_current(e,self.last_time)
            return self.provider._commit(prior[1],fingerprint,self.policy)
        if req.expected_version!=state.state_version:raise ValueError()
        if req.action in TRANSITIONS:
            before,after,_,reasons=TRANSITIONS[req.action]
            if state.state!=before or req.reason not in reasons:raise ValueError()
            if state.evidence_id is not None and state.evidence_id!=ref.evidence_id:raise ValueError()
        else:after='REJECTED' if req.action=='REJECT' else 'REVOKED'
        grant=self._actor(actor,ref.source_ids if ref else (cid,),role)
        if ref is not None:self._evidence_current(e,self.last_time)
        record=TransitionRecord(index=self.provider._index(),previous_audit_id=self.provider._head(),lifecycle_id=state.lifecycle_id,
            candidate=state.candidate,policy_id=req.policy_id,actor=grant,request_id=req.request_id,request_digest=req.digest,
            before=state.state,after=after,version_before=state.state_version,version_after=state.state_version+1,
            action=req.action,reason=req.reason,occurred_at=self.last_time,evidence=ref)
        return self.provider._commit(record,fingerprint,self.policy)

    def _require_approval(self,life):
        state=self._life(life)
        if state.state!='APPROVED_FOR_FURTHER_VALIDATION':raise ValueError()
        record=next(r for r in reversed(self.provider._audit()) if r.lifecycle_id==state.lifecycle_id)
        self._grant_by_id(record.actor.grant_id,(state.candidate.candidate_id,))
        entries=[e for e in self.evidences.values() if e.reference.evidence_id==state.evidence_id]
        if len(entries)!=1:raise ValueError()
        self._check_evidence(entries[0].handle,state.lifecycle_id);self._source(state.candidate.candidate_id)
        self._grant_by_id(record.actor.grant_id,record.evidence.source_ids)
        self._evidence_current(entries[0],self.last_time)
        if self._life(life)!=state:raise ValueError()
        return record

    def call(self,operation,*args,**kwargs):
        with self.lock:
            if self.busy:raise ValueError()
            self.busy=True
            try:return operation(*args,**kwargs)
            finally:self.busy=False


def _guard(operation,*args,**kwargs):
    try:return operation(*args,**kwargs)
    except Exception:pass
    reject_lifecycle()


class CandidateLifecycleOperator:
    """Host-only authority. Retain privately; never return to request handlers."""
    def __init__(self,root):self._root=root;self._self=self
    def _invoke(self,operation,*args):
        if self._self is not self or self._root.operator is not self:reject_lifecycle()
        with self._root.lock:return _guard(operation,*args)
    def grant(self,assertion):return self._invoke(self._root._grant,assertion)
    def revoke_actor(self,handle):return self._invoke(self._root._revoke_actor,handle)
    def authority_audit(self):return self._invoke(lambda:self._root.authority_events)
    def precommit_audit(self):return self._invoke(lambda:tuple(p.record for p in self._root.precommits.values()))
    def disclosure_audit(self):return self._invoke(lambda:self._root.disclosures)


class CandidateLifecycleService:
    def __init__(self,root):self._root=root;self._self=self
    def _invoke(self,operation,*args,**kwargs):
        if self._self is not self or self._root.service is not self:reject_lifecycle()
        return _guard(self._root.call,operation,*args,**kwargs)
    def register(self,actor,request):return self._invoke(self._root._register,actor,request)
    def capture(self,actor,lifecycle,request,*,precommit=None):return self._invoke(self._root._capture,actor,lifecycle,request,precommit)
    def precommit(self,actor,lifecycle,validation_evidence,test_request):return self._invoke(self._root._precommit,actor,lifecycle,validation_evidence,test_request)
    def decide(self,actor,lifecycle,request,*,evidence=None):return self._invoke(self._root._decide,actor,lifecycle,request,evidence)
    def inspect(self,lifecycle):return self._invoke(self._root._life,lifecycle)
    def audit(self):return self._invoke(self._root.provider._audit)
    def require_further_validation_decision(self,lifecycle):return self._invoke(self._root._require_approval,lifecycle)
    def describe_evidence(self,actor,handle,lifecycle):
        def read():
            state=self._root._life(lifecycle);e=self._root._evidence_record(handle,state.lifecycle_id)
            self._root._actor(actor,e.reference.source_ids)
            e=self._root._check_evidence(handle,state.lifecycle_id)
            self._root._actor(actor,e.reference.source_ids);self._root._evidence_current(e,self._root.last_time)
            return e.reference,e.report
        return self._invoke(read)


def bootstrap_candidate_lifecycle(evaluator,policy,*,clock=lambda:datetime.now(timezone.utc)):
    """Trusted bootstrap only: no global trust registration or deployment IAM."""
    def build():
        root=_Root(evaluator,policy,clock)
        operator=CandidateLifecycleOperator(root);service=CandidateLifecycleService(root)
        root.operator=operator;root.service=service
        return operator,service
    return _guard(build)
