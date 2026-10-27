"""Service-layer composition of original owners and fresh source capture.

No new registry/storage or publication authority. Two independent R7-02 captures
are compared by the service: R7-02 has no owner-private post-capture verifier.
M9 freshness may reread authorized evaluation labels through original M8.
"""
from src.data.contracts.backtesting import identity
from src.data.contracts.dataset import operational_json
from src.services.candidate_packaging_service import CandidatePackagingService
from src.services.candidate_lifecycle_service import CandidateLifecycleService, LifecycleHandle
from src.services.candidate_evaluation_service import evidence
from src.services.canonical_adapter_training_service import CandidateReference
from src.services.canonical_runtime_snapshot_service import CanonicalRuntimeSnapshotService


class RuntimeModelUseEvidence:
    """Trusted owner bridge; existing services retain their repository/providers."""
    def __init__(self, packaging, candidate, lifecycle, lifecycle_handle, snapshots):
        if (type(packaging) is not CandidatePackagingService or type(candidate) is not CandidateReference
                or type(lifecycle) is not CandidateLifecycleService or type(lifecycle_handle) is not LifecycleHandle
                or type(snapshots) is not CanonicalRuntimeSnapshotService): raise ValueError('invalid runtime composition')
        self._packaging=packaging; self._candidate=candidate
        self._lifecycle=lifecycle; self._life=lifecycle_handle; self._snapshots=snapshots
        self._self=self

    def verify(self, request, checkpoint, cancelled):
        if self._self is not self: raise ValueError()
        checkpoint()
        # A caller's snapshot object/digest never replaces original capture.
        snapshot=self._snapshots.capture(request.snapshot_request, cancelled=cancelled)
        checkpoint()
        if snapshot.snapshot_id != request.snapshot_id: raise ValueError()
        manifest=self._packaging.verify(request.package_id, candidate=self._candidate)
        checkpoint()
        record,metadata=self._packaging._owned(self._candidate)
        expected=(manifest.candidate_id,manifest.package_id,metadata.specification.family,
            manifest.representation_binding_id,metadata.representation_compatibility_id,
            manifest.preprocessing.state_id,manifest.preprocessing.canonical_state_id,
            manifest.preprocessing.canonical_schema_id)
        actual=(request.candidate_id,request.package_id,request.family,request.representation_binding_id,
            request.representation_compatibility_id,request.frozen_state_id,request.canonical_state_id,request.canonical_schema_id)
        if actual != expected or request.snapshot_request.dataset != manifest.plan.datasets[0]: raise ValueError()
        if request.snapshot_request.forecast_origin < manifest.plan.folds[0].model_fit_cutoff: raise ValueError()
        compatibility=self._snapshots.validate_preparation_context(request.snapshot_request, record[4], model_family=request.family)
        checkpoint()
        if compatibility.preparation_state_id != request.frozen_state_id: raise ValueError()
        # Pin the exact original M6 reference retained by original M9/M8 too.
        bound=self._lifecycle._root.evaluator._bindings.get(request.candidate_id)
        if bound is None or bound.candidate is not self._candidate or bound.packaging is not self._packaging or bound.package_id != request.package_id:
            raise ValueError()
        transition=self._lifecycle.require_further_validation_decision(self._life)
        checkpoint()
        state=self._lifecycle.inspect(self._life)
        if (state.lifecycle_id != request.lifecycle_id or state.state != 'APPROVED_FOR_FURTHER_VALIDATION'
                or transition.transition_id != request.lifecycle_transition_id or state.head_id != transition.transition_id
                or transition.candidate != evidence(manifest) or transition.evidence is None
                or transition.evidence.report_id != request.evaluation_report_id
                or transition.evidence.ensemble_evaluation_id != request.ensemble_evaluation_id): raise ValueError()
        if self._packaging._owned(self._candidate)[0] is not record: raise ValueError()
        checkpoint()
        # Only canonical validated identity references escape this bridge.
        return identity(['r7_03_verified_readiness_evidence_v1',request.request_id,manifest.package_id,
            snapshot.snapshot_id,transition.transition_id,transition.evidence.evidence_id,
            compatibility.model_dump(mode='json',warnings='error'),
            identity(operational_json(snapshot.model_dump(mode='json',warnings='error'))) ])
