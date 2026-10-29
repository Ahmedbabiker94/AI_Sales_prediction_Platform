"""Pure, model/source-independent B2 -> proposed PostgreSQL bytes. NO SQL/I/O.

This projection grants no authority. Host reconstructs it independently from the
full validated original-order B2 records, never from worker digest strings alone.
"""
from src.data.contracts.dataset import operational_json
from src.data.contracts.runtime_snapshot import _schema_from_json
from src.data.contracts.b3_forecast_write_intent import B3ForecastRecordSet
from src.data.contracts.forecast_evidence_storage import (
    CanonicalForecastStorageBatch, ForecastStorageScope, ForecastStorageLineage,
    ForecastLogicalKey, ForecastEvidenceRow,
)
from src.handoff.protocol import HandoffError


def project(records):
    if type(records) is not B3ForecastRecordSet: raise HandoffError('exact records required')
    records = B3ForecastRecordSet.model_validate_json(records.canonical_json)
    p = records.producer; e = p.execution; req = e.request
    inp = req.inputs; source = inp.request.snapshot_request; dataset = source.dataset.reference
    cfg = _schema_from_json(source.feature_schema_json).configuration
    lineage = ForecastStorageLineage(dataset_version=dataset.version_id, publication_id=source.dataset.admission_digest,
        capture_reference=source.request_id, capture_cutoff=source.capture_cutoff,
        artifact_id=req.artifact_id, artifact_sha256=req.payload_sha256,
        executable_input_id=inp.binding_id, snapshot_id=inp.request.snapshot_id,
        representation_id=inp.representation_id, frozen_state_id=inp.request.frozen_state_id,
        owner_reference=req.candidate_id, execution_id=e.result_id, producer_issuance_id=p.receipt_id,
        context_json=operational_json({'producer_id':p.producer_id, 'record_set_id':records.record_set_id,
            'row_set_sha256':records.row_set_sha256, 'request_id':req.request_id,
            'owner_reference_semantics':'candidate_reference_verified_original_worker_v1'}))
    rows = []
    for r in records.rows:
        key = ForecastLogicalKey(organization_id=dataset.organization_id, dataset_id=dataset.dataset_id,
            series_id=r.scope.series_id, candidate_id=req.candidate_id, family=req.family,
            forecast_origin=source.forecast_origin, target_period=r.scope.target_period,
            target_period_end=r.scope.target_period_end, target_metric=cfg.target_metric,
            measurement_json=operational_json(cfg.execution.measurement.model_dump(mode='json')),
            temporal_json=operational_json(cfg.execution.temporal.model_dump(mode='json')))
        if key.logical_key_id != r.logical_key_id: raise HandoffError('logical key mismatch')
        rows.append(ForecastEvidenceRow(key=key, lineage=lineage, float32_le=r.float32_le))
    return CanonicalForecastStorageBatch(scope=ForecastStorageScope(organization_id=dataset.organization_id,
        dataset_id=dataset.dataset_id, series_ids=tuple(sorted(source.dataset.series_ids))),
        idempotency_id=records.idempotency_id, rows=tuple(rows))


def inspect_proposal(body):
    records = B3ForecastRecordSet.model_validate_json(body['records'])
    batch = project(records)
    if records.canonical_json != body['records'] or batch.canonical_json != body['projection']:
        raise HandoffError('noncanonical or substituted projection')
    return records, batch
