"""M8 private shared truth capture over the accepted canonical provider protocol."""
from contextlib import contextmanager
from src.data.contracts.backtesting import identity, point
from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.candidate_evaluation import GroundTruthObservation
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.canonical_training_preparation_service import CanonicalTrainingPreparationService
from src.services.canonical_forecast_evaluation_service import _capture_label_rows, _verified_label_claim


class _PinnedRead:
    """Lend the ONE already-open session to the accepted capture helper.

    Fresh require_current is deferred until that session closes, avoiding nested
    repository flock acquisition. The original reader checks access throughout.
    """
    def __init__(self,session):self.session=session;self.used=False;self.checked=False
    @contextmanager
    def open_read(self,reference,*,purpose,budgets):
        if self.used or reference!=self.session.metadata.reference or purpose!=self.session.metadata.purpose:raise ValueError()
        self.used=True
        yield self.session
    def require_current(self,metadata,*,budgets):
        if metadata is not self.session.metadata or not self.used or not self.session.completed:raise ValueError()
        self.checked=True


def _budgets(request):
    b=request.resources
    return StagingBudgets(max_rows=b.max_capture_rows,max_elapsed_seconds=b.max_elapsed_seconds,
        max_raw_bytes=b.max_decoded_bytes,max_canonical_bytes=b.max_decoded_bytes,
        max_chunk_bytes=min(8*1024*1024,b.max_decoded_bytes),
        max_metadata_bytes=min(2*1024*1024,b.max_intermediate_bytes))


class CandidateEvaluationProvider:
    def __init__(self,provider,*,authorize_labels,verify_label_revision):
        if (type(provider) is not CanonicalTrainingDataProvider or not callable(authorize_labels)
                or not callable(verify_label_revision)):raise ValueError('invalid trusted evaluation composition')
        self._provider=provider;self._authorize=authorize_labels;self._verify_revision=verify_label_revision

    def _capture(self,request,engine,checkpoint,charge):
        # Only an original service composed by the operator invokes this method.
        def approve(stage):
            checkpoint()
            if self._authorize(request,stage) is not True:raise ValueError()
            checkpoint()
        approve('before_capture')
        binding=request.evaluation;b=request.resources
        budgets=_budgets(request)
        keys=request.keys;wanted={(k.scope.organization_id,k.scope.series_id,engine._time(k.scope.target_period)) for k in keys}
        with self._provider.open_read(binding.reference,purpose='offline_forecast_evaluation',budgets=budgets) as session:
            meta=session.metadata;checkpoint()
            if CanonicalTrainingPreparationService._temporal_binding(meta,binding.series_ids)!=binding:raise ValueError()
            definitions={s.series_id:s for s in meta.series}
            if set(definitions)!=set(binding.series_ids) or meta.rows>b.max_capture_rows:raise ValueError()
            for key in keys:
                s=key.scope
                if s.dimensions!=tuple(sorted(definitions[s.series_id].dimensions.items())):raise ValueError()
            fold=request.plan.folds[0];cutoff=engine._time(fold.assessment_cutoff)
            if cutoff>engine._time(self._provider.clock()):raise ValueError()
            adapter=_PinnedRead(session)
            rows=_capture_label_rows(adapter,(binding,),{binding.reference.organization_id:meta},wanted,engine,budgets,checkpoint,charge)
            if not adapter.checked:raise ValueError()
        self._provider.require_current(meta,budgets=budgets);checkpoint();approve('after_capture')
        truth=[]
        for key in keys:
            checkpoint();s=key.scope
            row=rows[(s.organization_id,s.series_id,engine._time(s.target_period))]
            claim=_verified_label_claim(row,s,meta,binding,cutoff,engine)
            if self._verify_revision(claim) is not True:raise ValueError()
            checkpoint()
            truth.append(GroundTruthObservation(key=key,value=point(row['value']),revision_claim_id=claim.digest))
        return tuple(truth),meta

    def _fresh(self,request,metadata,checkpoint):
        checkpoint()
        self._provider.require_current(metadata,budgets=_budgets(request))
        if self._authorize(request,'return') is not True:raise ValueError()
        checkpoint()
