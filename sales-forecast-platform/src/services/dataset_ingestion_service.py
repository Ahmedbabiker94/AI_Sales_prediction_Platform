"""Service-owned complete extraction. SEALED is private and never admitted."""
from contextlib import closing
from time import monotonic
from uuid import UUID

from src.data.adapters.base import SourceAdapter
from src.data.contracts.dataset import ConsistencyEvidence, StagingBudgets
from src.data.processing.canonical_mapper import CanonicalMapper
from src.providers import dataset_store_provider as _store_protocol
from src.providers.dataset_store_provider import StagingError


class DatasetIngestionService:
    def __init__(self, store, *, authorize_ingestion, verify_consistency=None, clock=monotonic):
        self.store = store
        self.authorize_ingestion = authorize_ingestion
        self.verify_consistency = verify_consistency
        self.clock = clock
        self.__completing = None
        self.__active = False

    def _claim_completion(self, attempt):
        """Repository-only one-use claim; never a public completion assertion."""
        if self.__completing is not attempt:
            raise StagingError('extraction lifecycle has not completed')
        self.__completing = None

    def ingest(self, adapter, mapping, *, dataset_id, budgets=None, batch_size=1000,
               consistency=None, cancelled=None):
        if not isinstance(adapter, SourceAdapter) or type(dataset_id) is not UUID:
            raise StagingError('invalid ingestion inputs')
        budgets = budgets or StagingBudgets()
        if type(budgets) is not StagingBudgets:
            raise StagingError('invalid staging budgets')
        evidence = consistency or ConsistencyEvidence()
        if type(evidence) is not ConsistencyEvidence:
            raise StagingError('invalid consistency evidence')
        if self.__active:
            raise StagingError('ingestion lifecycle already active')
        self.__active = True
        attempt = None
        extraction_completed = False
        started = None
        def check():
            if self.clock() - started >= budgets.max_elapsed_seconds:
                raise StagingError('staging elapsed-time budget exceeded')
            if cancelled is not None and cancelled():
                raise StagingError('staging cancelled')
        def authorize():
            try:
                permitted = self.authorize_ingestion(adapter, mapping.organization_id, dataset_id)
            except Exception:
                raise StagingError('ingestion authorization failed') from None
            if permitted is not True:
                raise StagingError('ingestion is not authorized')
        try:
            # Own cleanup even when initial authorization or mapping fails.
            with adapter:
                started = self.clock()
                check(); authorize()
                mapper = CanonicalMapper(mapping)
                asset = adapter.asset.model_copy(deep=True)
                if asset.organization_id != mapper.mapping.organization_id:
                    raise StagingError('ingestion organization scope mismatch')
                if evidence.kind != 'upstream_unverified':
                    try:
                        valid = self.verify_consistency is not None and self.verify_consistency(adapter, evidence) is True
                    except Exception:
                        raise StagingError('consistency verification failed') from None
                    if not valid:
                        raise StagingError('consistency evidence is not verified')
                attempt = self.store._begin(dataset_id=dataset_id, asset=asset, mapping=mapper.mapping,
                                            budgets=budgets, consistency=evidence, owner=self)
                with closing(adapter.extract(batch_size=batch_size)) as stream:
                    for raw in stream:
                        check(); authorize()
                        # Validate a detached mapping on every batch; detect caller drift.
                        if mapping.model_dump() != mapper.mapping.model_dump():
                            raise StagingError('mapping changed during extraction')
                        if len(raw.records) > budgets.max_rows or raw.records.shape[1] > budgets.max_columns:
                            raise StagingError('incoming batch budget exceeded')
                        canonical = mapper.map_batch(raw)
                        self.store._append(attempt, raw, canonical)
                        check()
                check()
                extraction_completed = True
            if not extraction_completed:
                raise StagingError('extraction did not complete normally')
            # Generator exhaustion AND generator.close AND adapter.__exit__ succeeded.
            check(); authorize()
            if mapping.model_dump() != mapper.mapping.model_dump():
                raise StagingError('mapping changed during cleanup')
            self.__completing = attempt
            return self.store._seal(attempt, owner=self, checkpoint=check)
        except BaseException as error:
            if attempt is not None:
                try:
                    self.store._abort(attempt)
                except Exception:
                    # An unavailable catalog still leaves only private STAGING data.
                    pass
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            raise StagingError('dataset extraction or staging failed') from None
        finally:
            self.__completing = None
            self.__active = False
            if attempt is not None:
                self.store._release(attempt)


_store_protocol._lifecycle_owner_type = DatasetIngestionService
