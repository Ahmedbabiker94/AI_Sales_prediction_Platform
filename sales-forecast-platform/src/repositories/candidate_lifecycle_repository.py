"""Bounded process-local CAS ledger; no filesystem, database or registry writes."""
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from src.data.contracts.candidate_lifecycle import TransitionRecord,LifecycleState,ZERO,reject_lifecycle


@dataclass(frozen=True)
class _Snapshot:
    states:object
    latest:object
    requests:object
    audit:tuple


class CandidateLifecycleRepository:
    def __init__(self):
        self._lock=RLock()
        self._data=_Snapshot(MappingProxyType({}),MappingProxyType({}),MappingProxyType({}),())

    def _swap(self,snapshot):
        # The only commit point. Tests may fail before this assignment; no
        # callback, I/O or partial mutation follows inside this transaction.
        self._data=snapshot

    def _append(self,record,fingerprint,max_lifecycles,max_transitions):
        with self._lock:
            old=self._data;key=(record.lifecycle_id,record.request_id)
            previous=old.requests.get(key)
            if previous is not None:
                if previous[0]!=fingerprint:raise ValueError()
                return previous[1],old.states[record.lifecycle_id],True
            current=old.states.get(record.lifecycle_id)
            if len(old.audit)>=max_transitions:raise ValueError()
            if record.index!=len(old.audit)+1 or record.previous_audit_id!=(old.audit[-1].transition_id if old.audit else ZERO):raise ValueError()
            if current is None:
                if record.action!='REGISTER' or len(old.states)>=max_lifecycles:raise ValueError()
                latest=old.latest.get(record.candidate.candidate_id)
                if latest!=record.previous_lifecycle_id:raise ValueError()
                if latest is not None and old.states[latest].state not in ('REJECTED','REVOKED'):raise ValueError()
            elif (record.candidate!=current.candidate or record.policy_id!=current.policy_id
                    or record.before!=current.state or record.version_before!=current.state_version):raise ValueError()
            state=LifecycleState(lifecycle_id=record.lifecycle_id,candidate=record.candidate,policy_id=record.policy_id,
                state=record.after,state_version=record.version_after,head_id=record.transition_id,
                evidence_id=record.evidence.evidence_id if record.evidence is not None else current.evidence_id if current else None,
                previous_lifecycle_id=record.previous_lifecycle_id if current is None else current.previous_lifecycle_id)
            states=dict(old.states);states[state.lifecycle_id]=state
            latest=dict(old.latest);latest[state.candidate.candidate_id]=state.lifecycle_id
            requests=dict(old.requests);requests[key]=(fingerprint,record)
            snapshot=_Snapshot(MappingProxyType(states),MappingProxyType(latest),MappingProxyType(requests),old.audit+(record,))
            self._swap(snapshot)
            return record,state,False


def verify_lifecycle_audit(records,expected_head):
    """Verify structural integrity only; a copied chain grants no authority."""
    try:
        if type(records) is not tuple or len(records)>512:raise ValueError()
        previous=ZERO;states={};latest={};seen=set()
        for i,original in enumerate(records,1):
            if type(original) is not TransitionRecord:raise ValueError()
            record=TransitionRecord.model_validate_json(original.canonical_json)
            if record.index!=i or record.previous_audit_id!=previous or (record.lifecycle_id,record.request_id) in seen:raise ValueError()
            seen.add((record.lifecycle_id,record.request_id));old=states.get(record.lifecycle_id)
            if old is None:
                if record.action!='REGISTER' or latest.get(record.candidate.candidate_id)!=record.previous_lifecycle_id:raise ValueError()
                if record.previous_lifecycle_id is not None and states[record.previous_lifecycle_id].after not in ('REJECTED','REVOKED'):raise ValueError()
            elif (record.before!=old.after or record.version_before!=old.version_after
                    or record.candidate!=old.candidate or record.policy_id!=old.policy_id):raise ValueError()
            states[record.lifecycle_id]=record;latest[record.candidate.candidate_id]=record.lifecycle_id;previous=record.transition_id
        if previous!=expected_head:raise ValueError()
        return previous
    except Exception:pass
    reject_lifecycle()
