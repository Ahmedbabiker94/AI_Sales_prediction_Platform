"""HOST-SIDE opt-in genuine integration; no native libraries invoked by host.

Run only with explicit authorization: python this-file --authorized-native-handoff.
All fitting/loading/prediction occurs in the fixed network-none worker.
"""
import json
from pathlib import Path
import runpy
import struct
import sys
from uuid import UUID


def approved_synthetic_intent(records,batch):
    """Independent host policy for the accepted synthetic experiment, not a digest.

    New publication/candidate IDs are validated structurally and authenticated by
    the pinned original worker path. They are explicitly approved as new fixture
    identities here, never treated as a production candidate/ownership registry.
    """
    from src.data.contracts.runtime_snapshot import _schema_from_json
    req=records.producer.execution.request; src=req.inputs.request.snapshot_request
    cfg=_schema_from_json(src.feature_schema_json).configuration
    assert cfg.ordered_outputs==('location','lag1','promotion')
    # Canonical feature-engine lag declaration is float64; unchanged M3 strict
    # ingress physically supplies exact float32 (bound separately below).
    assert dict(cfg.output_dtypes)=={'location':'int8','lag1':'float64','promotion':'int8'}
    measurement=cfg.execution.measurement
    assert (measurement.metric,measurement.unit,measurement.currency)==('reported_sales','monetary_amount','SAR')
    temporal=cfg.execution.temporal
    assert temporal.frequency.value=='weekly' and temporal.timezone=='UTC' and temporal.week_start==0
    assert temporal.calendar=='gregorian' and temporal.period_anchor=='start'
    assert temporal.lag_basis=='period' and temporal.gap_policy==temporal.cold_start=='reject'
    assert len(cfg.execution.exogenous)==1
    exo=cfg.execution.exogenous[0]
    assert exo.field=='promotion' and exo.availability=='known_in_advance' and exo.missing=='reject'
    assert batch.scope.organization_id==UUID(int=2) and batch.scope.dataset_id==UUID(int=102)
    assert len(batch.scope.series_ids)==len(records.rows)==4
    assert batch.scope.series_ids==tuple(sorted(src.dataset.series_ids))
    assert req.family=='xgboost' and req.inputs.selection.adapter=='canonical_m3_xgboost_inputs_v1'
    assert req.inputs.selection.representation=='tabular' and req.inputs.native_ingress_dtype=='float32'
    assert src.forecast_origin.isoformat()=='2020-02-17T00:00:00+00:00'
    assert src.capture_cutoff==src.forecast_origin
    assert src.static_fields==('location','department') and src.external_fields==('promotion',)
    assert records.shape==(4,) and records.physical_dtype=='float32'
    expected=('2d56f943','e9542244','2d56f943','e9542244')
    assert tuple(r.float32_le for r in records.rows)==expected
    assert tuple(r.float32_le for r in batch.rows)==expected
    assert [r.logical_key_id for r in records.rows]==[r.key.logical_key_id for r in batch.rows]
    for row in batch.rows:
        assert row.key.target_metric=='reported_sales'
        assert row.key.forecast_origin==row.key.target_period==src.forecast_origin
        assert row.key.target_period_end.isoformat()=='2020-02-24T00:00:00+00:00'
        assert row.lineage.artifact_id==req.artifact_id and row.lineage.artifact_sha256==req.payload_sha256
        assert row.lineage.snapshot_id==req.inputs.request.snapshot_id
        assert row.lineage.executable_input_id==req.inputs.binding_id
        assert row.lineage.frozen_state_id==req.inputs.request.frozen_state_id
        assert row.lineage.representation_id==req.inputs.representation_id
        assert row.lineage.producer_issuance_id==records.producer.receipt_id
    assert batch.correction_policy==records.correction_policy=='INITIAL_WRITE_ONLY'
    return True


def main():
    if sys.argv[1:]!=['--authorized-native-handoff']:
        raise SystemExit('explicit bounded native authorization required')
    host=runpy.run_path(str(Path(__file__).with_name('run_native_handoff.py')))
    result=host['run'](allow_native_handoff=True,use_recovered_image=True,approve=approved_synthetic_intent)
    assert result['state']=='VALIDATED_NO_WRITE' and not result['writes_performed']
    assert result['durable_receipt'] is None and result['cleanup']=='CLEANUP_SUCCESS' and result['worker_exit']==0
    records=json.loads(result['records']); bits=[r['float32_le'] for r in records['rows']]
    values=[struct.unpack('<f',bytes.fromhex(x))[0] for x in bits]
    assert values==[498.6732482910156,649.3267211914062,498.6732482910156,649.3267211914062]
    print(json.dumps(dict(state=result['state'],session=result['session'],release=result['release'],
        record_set_id=result['record_set_id'],projection_sha256=result['projection_sha256'],
        predictions=values,float32_le=bits,shape=records['shape'],dtype=records['physical_dtype'],
        artifact_sha256=records['producer']['execution']['request']['payload_sha256'],
        original_worker_chain='M6/B1/B2/B3/producer/write_intent/protected_release',
        worker_exit=result['worker_exit'],cleanup=result['cleanup'],worker_identity=result['worker_identity'],
        isolation=result['isolation'],lifecycle=result['lifecycle'],writes_performed=False,durable_receipt=None),sort_keys=True))


if __name__=='__main__': main()
