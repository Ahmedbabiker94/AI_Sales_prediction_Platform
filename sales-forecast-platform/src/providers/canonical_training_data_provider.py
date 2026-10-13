"""Authorized read-only FINALIZED versions; not wired into active training."""
from datetime import datetime, timezone
from time import monotonic

from src.data.contracts.dataset import StagingBudgets
from src.providers.dataset_store_provider import StagingError
from src.services.dataset_finalization_service import trusted_callback, validate_admission_binding, verify_dataset_quality_evidence


class CanonicalTrainingDataProvider:
    def __init__(self, store, *, authorize_read, clock=lambda:datetime.now(timezone.utc), elapsed_clock=monotonic):
        self.store=store;self.authorize_read=authorize_read;self.clock=clock;self.elapsed_clock=elapsed_clock

    def read_batches(self, reference, *, purpose, budgets=None):
        budgets=budgets or StagingBudgets()
        try:
            start=self.elapsed_clock()
            def checkpoint():
                if self.elapsed_clock()-start>=budgets.max_elapsed_seconds:raise StagingError('read elapsed budget exceeded')
                trusted_callback(self.authorize_read,'dataset read is not authorized',reference,purpose)
            checkpoint()
            with self.store._publication_session(budgets):
                candidate=self.store._candidate(reference,budgets)
                if candidate.state!='finalized':raise StagingError('dataset is not finalized')
                admission=self.store._publication(reference,budgets)
                if admission is None:raise StagingError('finalized admission evidence missing')
                data,mapping=validate_admission_binding(admission,candidate,purpose,self.clock())
                candidate.mapping_json=mapping.model_dump_json()
                # All payloads and lineage verified before the first visible batch.
                with self.store._read_snapshot(candidate,checkpoint) as batches:
                    with self.store._quality_key_index(budgets) as record_key:
                        verify_dataset_quality_evidence(batches(),mapping,record_key,data)
                    for frame in batches():
                        checkpoint()
                        validate_admission_binding(admission,candidate,purpose,self.clock())
                        yield frame
        except GeneratorExit:
            raise
        except Exception:
            raise StagingError('finalized dataset access failed') from None
