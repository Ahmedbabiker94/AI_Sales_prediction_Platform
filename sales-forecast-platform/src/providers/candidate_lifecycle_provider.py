"""Private adapter to the decision ledger, not a production model registry."""
from src.data.contracts.backtesting import identity
from src.data.contracts.candidate_lifecycle import DecisionOutcome,ZERO
from src.repositories.candidate_lifecycle_repository import CandidateLifecycleRepository,verify_lifecycle_audit


class CandidateLifecycleProvider:
    def __init__(self,repository):
        if type(repository) is not CandidateLifecycleRepository:raise ValueError()
        self._repository=repository
    def _state(self,life):return self._repository._data.states.get(life)
    def _latest(self,candidate):return self._repository._data.latest.get(candidate)
    def _prior(self,life,request_id):return self._repository._data.requests.get((life,request_id))
    def _head(self):
        records=self._repository._data.audit
        return records[-1].transition_id if records else ZERO
    def _index(self):return len(self._repository._data.audit)+1
    def _commit(self,record,fingerprint,policy):
        transition,current,repeated=self._repository._append(record,fingerprint,policy.max_lifecycles,policy.max_transitions)
        return DecisionOutcome(status='IDEMPOTENT' if repeated else 'APPLIED',transition=transition,current=current)
    def _audit(self):
        records=self._repository._data.audit
        verify_lifecycle_audit(records,self._head())
        return records
