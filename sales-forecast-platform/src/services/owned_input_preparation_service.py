"""B2 family-neutral composition of original ownership, R7-02 and allowlisted inputs.

No native artifact, framework, execution permission, predictor or writer dependency.
"""
from threading import Lock
from time import monotonic
import math
from src.data.contracts.executable_inputs import (
    ExecutableInputRequest, ExecutableInputBinding, OwnedInputEnvelope, InputPreparationOutcome,
)
from src.data.contracts.features import FeatureState
from src.data.contracts.training_adapter import TrainingCandidateMetadata
from src.data.contracts.runtime_snapshot import _schema_from_json
from src.preprocessing.runtime_input_adapters import selection_for, adapter_for, AdapterUnavailable
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.repositories.read_only_dataset_repository import ReadOnlyDatasetRepository
from src.services.canonical_adapter_training_service import CanonicalAdapterTrainingService, CandidateReference
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService


class OwnedInputPreparationService:
    def __init__(self, trainer, snapshots, *, authorize_inputs, elapsed_clock=monotonic):
        if (type(trainer) is not CanonicalAdapterTrainingService or type(snapshots) is not CanonicalRuntimeSnapshotService
                or type(snapshots._provider) is not CanonicalTrainingDataProvider
                or type(snapshots._provider.store) is not ReadOnlyDatasetRepository
                or not callable(authorize_inputs) or not callable(elapsed_clock)):
            raise ValueError('read-only original input composition required')
        self._trainer = trainer; self._snapshots = snapshots; self._authorize = authorize_inputs
        self._clock = elapsed_clock; self._self = self; self._lock = Lock(); self._issued = {}

    def _owned(self, reference):
        if self._self is not self or type(reference) is not CandidateReference: raise ValueError()
        record = self._trainer._candidates.get(id(reference))
        if record is None or record[0] is not self._trainer or record[1] is not reference: raise ValueError()
        m = TrainingCandidateMetadata.model_validate_json(record[2].canonical_json)
        selection, state = selection_for(m, record[4])
        return record, m, selection, state

    def prepare(self, reference, request, *, cancelled=lambda:False):
        deny = lambda reason:InputPreparationOutcome('DENIED', reason)
        if self._self is not self or not self._lock.acquire(blocking=False): return deny('BUSY_OR_INVALID_OWNER')
        try:
            if type(request) is not ExecutableInputRequest or not callable(cancelled): raise ValueError()
            r = ExecutableInputRequest.model_validate_json(request.canonical_json)
            if len(r.canonical_bytes) > r.limits.max_request_bytes: raise ValueError()
            record, m, selection, state = self._owned(reference)
            if (r.candidate_id != m.candidate_id or r.family != selection.family
                    or r.frozen_state_id != selection.frozen_state_id or r.compatibility_id != selection.compatibility_id): raise ValueError()
            adapter = adapter_for(selection)
            if adapter is None: return InputPreparationOutcome('UNAVAILABLE','INPUT_ADAPTER_UNAVAILABLE')
            if not adapter.supported(state): return InputPreparationOutcome('UNAVAILABLE','SOURCE_POLICY_UNAVAILABLE')
            original_provider = self._snapshots._provider
            repo = original_provider.store
            if type(repo) is not ReadOnlyDatasetRepository: raise ValueError()
            dataset = adapter.dataset(state)
            if (r.snapshot_request.dataset != dataset or repo._reference != dataset.reference
                    or repo._series != dataset.series_ids): raise ValueError()
            adapter.validate_context(r.snapshot_request, state)
            self._snapshots.validate_preparation_context(r.snapshot_request, state, model_family=r.family)
            previous = None; start = None
            source_clock = self._snapshots._clock
            def live():
                if self._snapshots._provider is not original_provider or original_provider.store is not repo: raise ValueError()
                current = self._trainer._candidates.get(id(reference))
                if current is not record or current[0] is not self._trainer or current[1] is not reference: raise ValueError()
                return current
            def tick():
                nonlocal previous, start
                # Source clock and outer clock callbacks both precede live lookup.
                source_now = source_clock()
                now = self._clock()
                if type(now) not in (int,float) or not math.isfinite(now): raise ValueError()
                if start is None: start = now
                if previous is not None and now < previous or now-start >= r.limits.max_elapsed_seconds: raise ValueError()
                previous = now
                live()
                return source_now
            def stop():
                stopped = cancelled() is not False
                if stopped: raise ValueError()
                return False
            def check():
                stop(); tick()
            def approve(stage):
                if self._authorize(m, r, stage) is not True: raise ValueError()
                check()
            check(); approve('admit')
            # Request-local accepted capture service with composed timing. The
            # accepted service/provider implementation and authority are reused.
            provider = CanonicalTrainingDataProvider(repo,authorize_read=original_provider.authorize_read,
                clock=original_provider.clock,elapsed_clock=original_provider.elapsed_clock)
            source = CanonicalRuntimeSnapshotService(provider,
                schema=_schema_from_json(self._snapshots._feature_schema_json),
                fitted_state=FeatureState.model_validate_json(self._snapshots._state_json),
                authorize_snapshot=self._snapshots._authorize, verify_history=self._snapshots._history,
                authorize_external_forecast=self._snapshots._external, elapsed_clock=tick)
            snapshot = source.capture(r.snapshot_request, cancelled=stop)
            if snapshot.snapshot_id != r.snapshot_id: raise ValueError()
            check()
            output = adapter.transform(snapshot,state,m)
            check()
            binding = ExecutableInputBinding(request=r,selection=selection,output_id=output.output_id,
                representation_id=output.representation.representation_id)
            if len(binding.canonical_bytes)+len(output.canonical_bytes) > r.limits.max_output_bytes: raise ValueError()
            envelope = OwnedInputEnvelope(binding,output)
            approve('return')
            final = source.capture(r.snapshot_request, cancelled=stop)
            # No clock/cancellation/authority callback after accepted terminal
            # capture. Its final joint clock has already checked both clocks and
            # original ownership. Sequential evidence checks are not leases.
            if final.canonical_bytes != snapshot.canonical_bytes or final.snapshot_id != r.snapshot_id: raise ValueError()
            live()
            if self._owned(reference)[0] is not record: raise ValueError()
            if len(self._issued) >= 8: raise ValueError()
            self._issued[id(envelope)] = (envelope, reference, record, binding.canonical_json, output.canonical_json)
            return InputPreparationOutcome('PREPARED_INPUTS','INPUT_DATA_ONLY',envelope)
        except Exception:
            return deny('INVALID_UNAUTHORIZED_OR_INTERRUPTED_INPUTS')
        finally: self._lock.release()

    def require(self, envelope):
        """Original data-handle integrity/live ownership, NOT source freshness or execution permission."""
        try:
            if self._self is not self or type(envelope) is not OwnedInputEnvelope: raise ValueError()
            item = self._issued.get(id(envelope))
            if item is None or item[0] is not envelope or self._owned(item[1])[0] is not item[2]: raise ValueError()
            if envelope.binding.canonical_json != item[3] or envelope.output.canonical_json != item[4]: raise ValueError()
            return envelope.output
        except Exception: pass
        raise ValueError('owned input data unavailable') from None
