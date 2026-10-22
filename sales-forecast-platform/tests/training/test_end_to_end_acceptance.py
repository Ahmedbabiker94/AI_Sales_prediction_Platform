"""D3-D: fresh, process-local offline acceptance; no production integrations.

The accepted D2 test factory supplies admitted training data and its independent
release ledger. Evaluation ingestion, approval and C0/C1 composition are explicit
here. Instrumentation is installed before invoking even that training factory.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import runpy
import struct
import subprocess
import traceback
from uuid import UUID
import warnings
import numpy as np

import pandas as pd
import pytest
import xgboost as xgb

from src.data.contracts.dataset import (
    ConsistencyPolicy, DatasetAccessContext, ScopedWarningAuthorization,
    StoredDatasetReference, VerifiedConsistencyEvidence,
)
from src.data.contracts.backtesting import ForecastScope
from src.data.contracts.forecast_evaluation import (
    BaselineDefinition, EvaluationBudgets, ForecastScoringPlan, ScoringArithmeticPolicy,
)
from src.data.contracts.model_evaluation import (
    PublicationRelationshipDeclaration, declare_evaluation_eligibility,
)
from src.data.contracts.model_joint_evaluation import JointH1ScoringBudgets
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.repositories.local_dataset_repository import LocalDatasetRepository
from src.services.canonical_baseline_evaluation_service import BaselineAuthorityBinding
from src.services.canonical_joint_model_prediction_service import _BaselineOwner
from src.services.canonical_model_prediction_service import _Owner, ModelPredictionError
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, ValueAttestedAvailabilityVerifier,
)
from src.services.dataset_finalization_service import DatasetFinalizationService
from src.services.dataset_ingestion_service import DatasetIngestionService
from src.services.training_evaluation_relationship_authority import bootstrap_relationship_authority


BASELINE = '262bffd1e00cff1b1d9b4f842918e02db0c117c8'
FROZEN = 'c7e42decc737296195db63af82f01437940270804daed1d7bddbcd587cd60adf'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def exact(value):
    return Fraction.from_float(float(value))


def reference(predictions, actuals):
    """Independent sufficient statistics: never call a production scorer."""
    errors = [exact(p) - exact(y) for p, y in zip(predictions, actuals, strict=True)]
    n = len(errors)
    sae = sum(map(abs, errors), Fraction())
    sse = sum((e * e for e in errors), Fraction())
    saa = sum((abs(exact(y)) for y in actuals), Fraction())
    return dict(count=n, sae=sae, sse=sse, saa=saa, mae=sae/n,
                mse=sse/n, rmse_radicand=sse/n,
                wape_status='defined' if saa else 'undefined_zero_denominator',
                wape=sae/saa if saa else None)


def assert_statistics(actual, expected):
    assert actual.count == expected['count']
    assert actual.wape_status == expected['wape_status']
    for name in ('sae', 'sse', 'saa', 'mae', 'mse', 'rmse_radicand', 'wape'):
        value = getattr(actual, name)
        assert (None if value is None else value.fraction) == expected[name]


class NativeGuard:
    """Test-only, irreversible latch at public AND actual XGBoost C entrypoints.

    Saved genuine Python methods still pass through the C-entrypoint guards.
    No arguments, model data or native results are retained in diagnostics.
    """
    def __init__(self, monkeypatch):
        from xgboost.core import _LIB
        self.owner=None
        self.sealed=False
        self.violations=[]
        self._reports=[]  # Private (report, original guard) identity bindings; never serialized.
        def install(target,name,label):
            original=getattr(target,name)
            def guarded(*args,**kwargs):
                self.check(label)
                return original(*args,**kwargs)
            monkeypatch.setattr(target,name,guarded)
        install(xgb,'train','xgboost.train')
        for name in ('predict','inplace_predict','load_model','update','boost'):
            install(xgb.Booster,name,'Booster.'+name)
        for name in ('XGBoosterPredictFromDense','XGBoosterPredictFromCSR',
                     'XGBoosterPredictFromColumnar','XGBoosterPredictFromDMatrix',
                     'XGBoosterPredictFromCudaArray','XGBoosterPredictFromCudaColumnar',
                     'XGBoosterLoadModel','XGBoosterLoadModelFromBuffer',
                     'XGBoosterUnserializeFromBuffer','XGBoosterUpdateOneIter',
                     'XGBoosterTrainOneIter','XGBoosterBoostOneIter'):
            if hasattr(_LIB,name):
                install(_LIB,name,'C.'+name)

    def check(self,label):
        if self.sealed or self.owner is not None and self.owner.phase=='SEALED':
            self.sealed=True
            self.violations.append(label)
            raise AssertionError('post-seal native operation attempted')

    def assert_clean(self):
        assert not self.violations, 'post-seal native operation attempted'


@pytest.fixture
def scenario(tmp_path, monkeypatch, request):
    """Each invocation trains a fresh candidate and creates fresh original owners."""
    trace = []
    phase = ['setup']
    native = []
    control = dict(deny=None, hook=None, revision=True)
    read = CanonicalTrainingDataProvider.open_read
    guard = NativeGuard(monkeypatch)
    revision_claims=[]
    scoring_budgets=JointH1ScoringBudgets(max_elapsed_seconds=10)

    @contextmanager
    def tracked_read(provider, *args, **kwargs):
        trace.append((phase[0], 'read_open', kwargs['purpose']))
        try:
            with read(provider, *args, **kwargs) as session:
                yield session
        finally:
            trace.append((phase[0], 'read_close', kwargs['purpose']))

    monkeypatch.setattr(CanonicalTrainingDataProvider, 'open_read', tracked_read)
    fit_state = CanonicalFeaturePipeline.fit_state
    prepare = CanonicalFeaturePipeline.prepare_training
    native_train = xgb.train
    native_predict = _Owner._predict_native
    native_load = _Owner._load_native
    baseline_execute = _BaselineOwner.execute

    def state(engine, frame, **kwargs):
        trace.append((phase[0], 'state_fit', None))
        assert phase[0] == 'training'
        assert (frame.period_end <= kwargs['training_cutoff']).all()
        assert (frame.available_at <= kwargs['training_cutoff']).all()
        return fit_state(engine, frame, **kwargs)

    def training(engine, history, requests, fitted, **kwargs):
        origin = kwargs['forecast_origin']
        trace.append((phase[0], 'training_origin', origin.isoformat()))
        assert origin in b['plan'].folds[0].training_origins
        assert (history.period_end <= origin).all() and (history.available_at <= origin).all()
        return prepare(engine, history, requests, fitted, **kwargs)

    def fit(*args, **kwargs):
        guard.check('xgboost.train')
        trace.append((phase[0], 'native_train', None))
        assert phase[0] == 'training'
        return native_train(*args, **kwargs)

    def predict(*args):
        values = native_predict(*args)
        assert phase[0] == 'c0'
        native.append(tuple(struct.pack('<f', float(v)).hex() for v in values))
        trace.append((phase[0], 'native_predict', None))
        return values

    def load(*args):
        trace.append((phase[0], 'native_load', None))
        assert phase[0] == 'c0'
        return native_load(*args)

    def baseline(owner, *args):
        trace.append((phase[0], 'baseline_execute', None))
        assert phase[0] == 'c0'
        return baseline_execute(owner, *args)

    monkeypatch.setattr(CanonicalFeaturePipeline, 'fit_state', state)
    monkeypatch.setattr(CanonicalFeaturePipeline, 'prepare_training', training)
    monkeypatch.setattr(xgb, 'train', fit)
    monkeypatch.setattr(_Owner, '_predict_native', staticmethod(predict))
    monkeypatch.setattr(_Owner, '_load_native', staticmethod(load))
    monkeypatch.setattr(_BaselineOwner, 'execute', baseline)

    # Reuse only the approved training-data/authority factory, not a preexecuted
    # C0/C1 fixture. All its setup reads are visible to the instrumentation above.
    factory = runpy.run_path('tests/training/test_canonical_xgboost_training.py')['make_owner'].__wrapped__(tmp_path/'training')
    b = factory()
    phase[0] = 'training'
    bundle = b['train']()
    assert bundle.manifest.runtime.xgboost == '3.2.0'
    assert sum(event == 'state_fit' for _, event, _ in trace) == 1
    assert sum(event == 'native_train' for _, event, _ in trace) == 1
    assert [(e, purpose) for p, e, purpose in trace if p == 'training' and e.startswith('read_')] == [
        ('read_open','production_training'), ('read_close','production_training')]
    assert len(bundle.manifest.provenance.labels) == 4
    fold = b['plan'].folds[0]
    assert all(c.forecast_origin in fold.training_origins and c.period_end <= fold.model_fit_cutoff
               and c.available_at <= fold.model_fit_cutoff for c in bundle.manifest.provenance.labels)
    phase[0] = 'setup'

    source = b['raws'][2].copy(deep=True)
    if getattr(request, 'param', None) == 'zero_truth':
        source.loc[source['from'] == b['at'](6), 'reported'] = 0.
    mapping = b['metadata'][0].mapping
    foundation = runpy.run_path('tests/data/adapters/test_integration_foundation.py')
    store = LocalDatasetRepository(tmp_path/'evaluation')
    now = b['provider'].clock()
    provider = CanonicalTrainingDataProvider(store, authorize_read=lambda *_: True, clock=lambda: now)
    asset = foundation['asset'].__wrapped__().model_copy(update={'timezone': mapping.timezone})
    manifest = DatasetIngestionService(store, authorize_ingestion=lambda *_: True).ingest(
        foundation['MemoryAdapter'](source, asset), mapping, dataset_id=UUID(int=800), batch_size=3)
    ref = StoredDatasetReference(manifest.organization_id, manifest.dataset_id, manifest.attempt_id, manifest.version_id)
    scope = DatasetAccessContext(ref, manifest.source_id, manifest.ingestion_batch_id, mapping.mapping_id,
        mapping.version, manifest.mapping_digest, mapping.target_metric, mapping.business_scope,
        'opaque-source', 'connection-1', 'offline_forecast_evaluation')
    consistency = VerifiedConsistencyEvidence(scope, 'snapshot', 'snapshot_external_v1', 'method-1', '1.0',
        now-timedelta(days=2), now-timedelta(hours=1), now-timedelta(minutes=1), now+timedelta(days=1))
    warning = ScopedWarningAuthorization(scope, 'NEGATIVE_VALUE', 'value', 'keep', 'd3d-approved-negative',
        now-timedelta(hours=1), now+timedelta(days=1))
    DatasetFinalizationService(store, authorize=lambda *_: True, verify_consistency=lambda *_: True,
        verify_policy=lambda *_: True, verify_warning=lambda *_: True,
        decision_context_reference='d3d-controlled-offline', clock=lambda: now).finalize(
            ref, mapping, connection_reference='connection-1', purpose='offline_forecast_evaluation',
            consistency_policy=ConsistencyPolicy(scope, 'SALES_GENERAL', '1.0.0'),
            consistency_evidence=consistency, warning_authorizations=(warning,))
    with provider.open_read(ref, purpose='offline_forecast_evaluation') as session:
        meta = session.metadata
        lineage = {r['source_record_id']: (r['series_id'], r['lineage_id'])
                   for frame in session.batches() for r in frame.to_dict('records')}
    ledger = {str(i): r for i, r in source.iterrows()}
    args = runpy.run_path('tests/data/contracts/test_model_evaluation.py')['_genuine_arguments'](bundle)
    binding = CanonicalTrainingPreparationService._temporal_binding(meta, args['relationship'].evaluation.series_ids)
    args['relationship'] = PublicationRelationshipDeclaration.model_validate({**args['relationship'].model_dump(),
        'evaluation': binding, 'authority_id': 'd3d-operator', 'authority_version': '1.0',
        'relationship_id': 'd3d-independent-relationship', 'evidence_references': ('d3d-approved-fixture',)})
    eligibility = declare_evaluation_eligibility(bundle, **args)
    clock = [0.]
    bo, bv = bootstrap_relationship_authority(b['provider'], provider,
        authority_id='d3d-operator', authority_version='1.0', issuance_context='d3d-offline', elapsed_clock=lambda: clock[0])
    approval = bo.approve(eligibility, training_metadata=b['metadata'][0], evaluation_metadata=meta,
                          approval_reference='d3d-approved-fixture')
    receipt = bv.verify(eligibility, approval)
    assert receipt.eligibility_id == eligibility.eligibility_id
    trace.append(('setup', 'relationship_approved', None))
    sealed_ref = []

    def decision(kind):
        def authorize(ctx, stage):
            assert ctx.purpose == 'offline_forecast_evaluation'
            if kind == 'label':
                sealed = sealed_ref[0]
                assert ctx.seal_id == sealed.seal_id and ctx.eligibility_id == eligibility.eligibility_id
                assert ctx.evaluation_publication == binding
                assert ctx.scopes == tuple(scope for scope in sealed.coverage.scopes
                    if ctx.forecast_origin is None or scope.forecast_origin == ctx.forecast_origin)
            else:
                assert ctx.eligibility_json == eligibility.model_dump_json()
                if kind == 'prediction':
                    assert ctx.relationship_approval_id == receipt.approval_id
            if ctx.forecast_origin is not None:
                assert ctx.forecast_origin in tuple(p.forecast_origin for p in eligibility.coverage.periods)
            trace.append((phase[0], kind, stage))
            if control['hook']:
                control['hook'](kind, stage)
            return control['deny'] != (kind, stage)
        return authorize

    def history(ctx, claims):
        assert ctx.metadata == meta
        for claim in claims:
            row = ledger[claim.source_record_id]
            assert (claim.series_id, claim.lineage_id) == lineage[claim.source_record_id]
            assert (claim.period_start, claim.period_end, claim.available_at, claim.evidence_reference) == (
                row['from'], row['to'], row['released'], row['proof'])
            assert claim.period_end <= ctx.forecast_origin and claim.available_at <= ctx.forecast_origin
        return True

    def history_values(ctx, claims):
        return all(c.availability.value_identity == ('float64', struct.pack('>d', float(ledger[c.availability.source_record_id]['reported'])).hex())
                   for c in claims)

    def seal(algorithm):
        phase[0] = 'c0'
        definition = BaselineDefinition(algorithm=algorithm, supported_horizon=1,
            lookback=4 if algorithm == 'seasonal_naive_v1' else 1,
            season_length=4 if algorithm == 'seasonal_naive_v1' else None)
        op, executor, verifier = b['owner'].bootstrap_joint_h1_prediction(bundle, eligibility=eligibility,
            provider=provider, relationship_verifier=bv, relationship_approval=approval,
            authorize_evaluation=decision('prediction'),
            verify_history=ValueAttestedAvailabilityVerifier(verify_availability=history, verify_captured_values=history_values),
            elapsed_clock=lambda: clock[0], baseline=definition,
            authorize_joint=decision('joint'), authorize_baseline=decision('baseline'))
        guard.owner=getattr(verifier,'_JointH1Verifier__owner')
        session = executor.execute(op.issue())
        guard.sealed=True
        sealed = verifier.verify(session)
        sealed_ref.append(sealed)
        assert sum(p == 'c0' and e == 'read_open' for p, e, _ in trace) == 1
        assert sum(p == 'c0' and e == 'native_load' for p, e, _ in trace) == 1
        assert sum(p == 'c0' and e == 'native_predict' for p, e, _ in trace) == 1
        assert tuple(e.native_float32_hex for e in sealed.model.events) == native[0]
        assert tuple(struct.pack('<f', e.point.value).hex() for e in sealed.model.events) == native[0]
        assert all(struct.pack('>d', e.point.value) == struct.pack('>d', struct.unpack('<f', bytes.fromhex(bits))[0])
                   for e, bits in zip(sealed.model.events, native[0], strict=True))
        assert sealed.eligibility.candidate.candidate_bundle_id == bundle.bundle_id
        assert sealed.model.fitted_state_digest == bundle.manifest.state_digest
        trace.append(('sealed', 'both_handoffs', None))

        def forbidden(*a, **kw):
            pytest.fail('post-seal producer operation')
        for name in ('_load_native', '_predict_native', '_joint_predict'):
            monkeypatch.setattr(_Owner, name, staticmethod(forbidden))
        monkeypatch.setattr(CanonicalFeaturePipeline, 'fit_state', forbidden)
        monkeypatch.setattr(CanonicalFeaturePipeline, 'prepare_inference', forbidden)
        monkeypatch.setattr(_BaselineOwner, 'execute', forbidden)
        import src.services.canonical_joint_model_prediction_service as joint_service
        monkeypatch.setattr(joint_service, '_owned_stateless_h1', forbidden)
        monkeypatch.setattr(b['owner'], 'verify_candidate', forbidden)
        monkeypatch.setattr(CanonicalTrainingPreparationService, '_capture', forbidden)
        phase[0] = 'c1'

        def revision(ctx, claims):
            trace.append(('c1', 'revision', None))
            assert ctx.seal_id == sealed.seal_id and ctx.evaluation_publication == binding
            if not control['revision']:
                return False
            for c in claims:
                row = ledger[c.source_record_id]
                assert (c.series_id, c.lineage_id) == lineage[c.source_record_id]
                assert (c.period_start, c.period_end, c.available_at, c.evidence_reference) == (
                    row['from'], row['to'], row['released'], row['proof'])
                assert c.value_identity == ('float64', struct.pack('>d', float(row['reported'])).hex())
                assert c.dataset_reference == ref and c.manifest_digest == meta.manifest_digest
                assert c.admission_digest == meta.admission_digest and c.mapping_digest == binding.mapping_digest
                assert c.source_id == meta.source_id and c.organization_id == ref.organization_id
                assert c.assessment_cutoff == ctx.assessment_cutoff and c.available_at <= ctx.assessment_cutoff
            revision_claims.append(tuple(claims))
            return True

        authority = BaselineAuthorityBinding('d3d-independent-label-ledger', '1.0', 'captured_value_as_of_E_v1', (binding,), revision)
        composition = dict(authorize_labels=decision('label'), label_revision_authority=authority,
                           budgets=scoring_budgets)
        label_op, evaluator = verifier.bootstrap_evaluation(session, **composition)
        return dict(op=op, verifier=verifier, session=session, sealed=sealed,
                    label_op=label_op, evaluator=evaluator, composition=composition)

    return dict(seal=seal, trace=trace, phase=phase, control=control, provider=provider,
                ledger=ledger, source=source, mapping=mapping, bo=bo, approval=approval,
                clock=clock, eligibility=eligibility, bundle=bundle, tmp=tmp_path,
                guard=guard,sealed=sealed_ref,revision_claims=revision_claims,
                scoring_budgets=scoring_budgets,lineage=lineage)


def wire_id(value):
    # Independent standard-library implementation of the documented JSON hash.
    return sha(json.dumps(value,sort_keys=True,separators=(',',':'),
                          ensure_ascii=False,allow_nan=False).encode())


def public_stats(values):
    return {k: ({'numerator':str(v.numerator),'denominator':str(v.denominator)}
                if isinstance(v,Fraction) else v) for k,v in values.items()}


def check_scores(s, result):
    # Derive scopes from ORIGINAL operator-approved geometry, never scored groups.
    d=s['eligibility'];relation=d.relationship
    assert result.sealed==s['sealed'][0]
    dimensions={series.series_id:series.dimensions for series in relation.series}
    scopes=tuple(ForecastScope(plan_digest=d.candidate.training_plan_digest,
        dataset_version=relation.evaluation.reference.version_id,fold_id=d.coverage.fold_id,
        organization_id=relation.evaluation.reference.organization_id,series_id=sid,
        metric=relation.metric,unit=relation.unit,currency=relation.currency,
        frequency=relation.frequency,timezone=relation.timezone,dimensions=dimensions[sid],
        forecast_origin=period.forecast_origin,horizon_step=1,target_period=period.target_period,
        target_period_end=period.target_period_end)
        for period in d.coverage.periods for sid in relation.evaluation.series_ids)
    assert result.sealed.coverage.scopes==scopes
    assert s['revision_claims'] and all(c==s['revision_claims'][0] for c in s['revision_claims'])
    claims=s['revision_claims'][0]
    assert len(claims)==len(scopes)
    cutoff=d.candidate.training_plan.folds[0].assessment_cutoff
    arithmetic=ScoringArithmeticPolicy(policy_id='canonical-exact-c1-v1')
    arithmetic_id=wire_id(arithmetic.model_dump(mode='json'))
    keys=[];actuals=[];label_ids=[]
    for scope in scopes:
        location=dict(scope.dimensions)['location']
        rows=[(rid,row) for rid,row in s['ledger'].items()
              if row['branch']==location and row['from']==scope.target_period]
        assert len(rows)==1
        rid,row=rows[0]
        actuals.append(row['reported'])
        sid,lineage=s['lineage'][rid]
        assert sid==scope.series_id
        binding=relation.evaluation;ref=binding.reference
        label_ids.append(wire_id([str(ref.organization_id),str(ref.dataset_id),str(ref.attempt_id),ref.version_id,
            binding.manifest_digest,binding.admission_digest,str(binding.source_id),binding.mapping_digest,
            str(scope.organization_id),str(scope.series_id),scope.metric,scope.unit,scope.currency,
            row['from'].isoformat(),row['to'].isoformat(),rid,str(lineage),row['released'].isoformat(),
            row['proof'],('float64',struct.pack('>d',float(row['reported'])).hex()),
            cutoff.isoformat(),'captured_value_as_of_E_v1']))
        keys.append((scope.fold_id,str(scope.organization_id),str(scope.series_id),1,
            scope.metric,scope.unit,scope.currency or '', 'validation',
            cutoff.isoformat(),scope.forecast_origin.isoformat()))
    assert tuple(label_ids)==result.label_revision_ids==tuple(c.digest for c in claims)
    expected_keys=tuple(sorted(set(keys)))
    expected_stats=[]
    for evaluation,handoff,points in (
        (result.model_scores,result.sealed.model_handoff_id,tuple(e.point for e in result.sealed.model.events)),
        (result.baseline_scores,result.sealed.baseline_handoff_id,result.sealed.baseline_points)):
        assert len(points)==len(scopes)==len(evaluation.events)
        assert tuple(point.scope for point in points)==scopes
        plan=ForecastScoringPlan(plan_id='joint-h1-c1',producer_plan_digest=handoff,
            datasets=(relation.evaluation,),partitions=('validation',),arithmetic=arithmetic,
            budgets=EvaluationBudgets(max_scored_events=s['scoring_budgets'].max_points,
                                      max_groups=s['scoring_budgets'].max_groups))
        assert evaluation.scoring_plan==plan and evaluation.producer_result_id==handoff
        plan_id=wire_id(plan.model_dump(mode='json'))
        expected_ids=[]
        for i,(scope,point,event,claim) in enumerate(zip(scopes,points,evaluation.events,claims,strict=True)):
            stats=reference([point.value],[actuals[i]])
            assert_statistics(event.statistics,stats)
            payload=dict(scope=scope.model_dump(mode='json'),
                producer_event_id=wire_id([handoff,i,point.model_dump(mode='json')]),
                label_binding_digest=label_ids[i],scoring_plan_digest=plan_id,
                arithmetic_policy_digest=arithmetic_id,
                assessment_cutoff=cutoff.isoformat().replace('+00:00','Z'),
                partition='validation',statistics=public_stats(stats))
            expected_id=wire_id(payload)
            assert event.model_dump(mode='json')==payload
            assert event.event_id==expected_id
            expected_ids.append(expected_id)
        assert len(set(expected_ids))==len(expected_ids)
        assert len(evaluation.groups)==len(expected_keys)
        groups={}
        observed_members=[]
        for key,group in zip(expected_keys,evaluation.groups,strict=True):
            # Compare EVERY semantic key field, policy binding and exact member.
            observed=(group.fold_id,str(group.organization_id),str(group.series_id),
                group.horizon_step,group.metric,group.unit,group.currency or '',group.partition,
                group.assessment_cutoff.isoformat(),group.forecast_origin.isoformat())
            assert observed==key
            assert group.scoring_plan_digest==plan_id and group.arithmetic_policy_digest==arithmetic_id
            assert group.label_revision_policy=='captured_value_as_of_E_v1'
            assert group.event_weighting=='origin_scoped_v1'
            indices=[i for i,k in enumerate(keys) if k==key]
            members=tuple(expected_ids[i] for i in indices)
            assert group.event_ids==members
            observed_members.extend(group.event_ids)
            expected=reference([points[i].value for i in indices],[actuals[i] for i in indices])
            assert_statistics(group.statistics,expected)
            groups[key]=expected
        assert len(observed_members)==len(set(observed_members))==len(expected_ids)
        assert set(observed_members)==set(expected_ids)
        expected_stats.append(groups)
    # Independently calculate the owned baseline from eligible raw history too.
    for scope,point in zip(scopes,result.sealed.baseline_points,strict=True):
        history=sorted((r for r in s['ledger'].values() if r['branch']==dict(scope.dimensions)['location']
            and r['to']<=scope.forecast_origin and r['released']<=scope.forecast_origin),key=lambda r:r['from'])
        assert exact(point.value)==exact(history[-result.sealed.baseline_definition.lookback]['reported'])
    assert len(result.deltas)==len(expected_keys)
    semantic=[]
    for key,delta in zip(expected_keys,result.deltas,strict=True):
        indices=[i for i,k in enumerate(keys) if k==key]
        forecast_ids=tuple(sorted(wire_id(scopes[i].model_dump(mode='json')) for i in indices))
        assert delta.forecast_key_ids==forecast_ids
        assert delta.semantic_group_id==wire_id(['joint_h1_c1',arithmetic_id,key,forecast_ids])
        model,baseline=(mapping[key] for mapping in expected_stats)
        assert_statistics(delta.candidate,model)
        assert_statistics(delta.baseline,baseline)
        differences={}
        for field in ('sae','sse','mae','mse','wape'):
            expected=None if model[field] is None else model[field]-baseline[field]
            value=getattr(delta,field)
            assert (None if value is None else value.fraction)==expected
            differences[field]=expected
        assert delta.wape_status==model['wape_status']
        assert not {'rmse','winner','improvement_percentage'} & set(type(delta).model_fields)
        semantic.append(dict(origin=scopes[indices[0]].forecast_origin.isoformat(),series=str(scopes[indices[0]].series_id),
            model=public_stats(model),baseline=public_stats(baseline),deltas=public_stats(differences)))
    return semantic


@pytest.mark.parametrize('algorithm', ['last_observation_v1', 'seasonal_naive_v1'])
def test_fresh_native_acceptance(scenario, algorithm, request):
    s = scenario
    b = s['seal'](algorithm)
    h = b['label_op'].issue()
    # Observe the original owner's claim before *any* C1 label-provider read.
    joint_owner = getattr(b['verifier'], '_JointH1Verifier__owner')
    original = s['provider'].open_read
    @contextmanager
    def checked(*a, **kw):
        assert joint_owner.consumption_state == 'LABELS_AUTHORIZED'
        assert joint_owner.consumption_owner is not None
        assert ('sealed', 'both_handoffs', None) in s['trace']
        assert ('c1', 'label', 'read') in s['trace']
        with original(*a, **kw) as session:
            yield session
    s['provider'].open_read = checked
    result = b['evaluator'].evaluate(h)
    assert result.status == 'TERMINAL_SCORED_NOT_PRODUCTION_APPROVED'
    semantic = check_scores(s, result)
    if algorithm == 'last_observation_v1':
        assert any(d.sae.fraction > 0 for d in result.deltas)
        assert any(d.sae.fraction < 0 for d in result.deltas)
    assert sum(p == 'c1' and e == 'read_open' for p, e, _ in s['trace']) == 1
    assert sum(p == 'c1' and e == 'read_close' for p, e, _ in s['trace']) == 1
    assert b['verifier'].verify(b['session']).result_id == result.sealed.result_id
    with pytest.raises(ModelPredictionError):
        b['evaluator'].evaluate(h)
    assert sum(p == 'c1' and e == 'read_open' for p, e, _ in s['trace']) == 1
    encoded = result.model_dump_json()
    assert type(result).model_validate_json(encoded).model_dump_json() == encoded
    s['guard'].assert_clean()
    # Explicitly build public evidence after assertions, never serialize owners.
    report = build_report(s, result, semantic, request.node.nodeid)
    path = s['tmp']/('acceptance-'+algorithm+'.json')
    write_public_report(report, path, guard=s['guard'])
    assert len(path.read_bytes()) <= 128*1024
    assert json.loads(path.read_text()) == report
    assert canonical(report['semantic']) == canonical(json.loads(canonical(report))['semantic'])
    print('D3D_EVIDENCE', path, sha(path.read_bytes()))


def build_report(s, result, semantic, nodeid):
    """Strict allowlist at every construction site; no arbitrary object dumping."""
    s['guard'].assert_clean()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    parent = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], text=True).strip()
    clean = not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
    frozen = sha(Path('tests/fixtures/reference_training_956fcb2.json').read_bytes())
    assert frozen == FROZEN
    # Timestamps are serialized explicitly; this hashes the approved source,
    # not publicizing its rows or using claim values as the arithmetic oracle.
    source = [[str(v) if isinstance(v, pd.Timestamp) else v for v in row]
              for row in s['source'].itertuples(index=False, name=None)]
    sealed = result.sealed
    report = dict(schema_version='d3d-test-evidence-v1',
        source=dict(commit=commit, parent=parent, accepted_baseline=BASELINE, tracked_source_clean=clean),
        fixtures=dict(scenario_sha256=sha(canonical(dict(columns=list(s['source'].columns),
                      dtypes=[str(d) for d in s['source'].dtypes],rows=source)).encode()),
                      mapping_sha256=sha(s['mapping'].model_dump_json().encode()), frozen_sha256=frozen),
        runtime=dict(python=platform.python_version(), **{k: importlib.metadata.version(k)
                     for k in ('pytest', 'xgboost', 'numpy', 'pandas', 'pydantic', 'pyarrow', 'scipy')}),
        tests=[dict(nodeid=nodeid, outcome='passed_assertions')],
        semantic=dict(scope='validation_only_h1', baseline_algorithm=sealed.baseline_definition.algorithm, groups=semantic),
        run=dict(candidate_id=sealed.model.candidate_bundle_id, eligibility_id=sealed.eligibility.eligibility_id,
            publication_id=sealed.eligibility.relationship.evaluation.reference.version_id,
            capture_id=sealed.capture_id, seal_id=sealed.seal_id, model_handoff_id=sealed.model_handoff_id,
            baseline_handoff_id=sealed.baseline_handoff_id, label_revision_ids=list(result.label_revision_ids),
            label_authority_id=result.label_authority_id, label_authority_version=result.label_authority_version,
            evaluation_id=result.evaluation_id),
        lifecycle=[list(e) for e in s['trace']],
        checks={k: True for k in ('training_only', 'genuine_native', 'original_owner', 'claim_before_labels',
            'one_history_capture', 'one_label_capture', 'independent_exact_scores', 'supported_deltas',
            'no_postseal_producers', 'replay_rejected', 'continued_c0_verification', 'stable_serialization')},
        diagnostics=['guarded_runtime_errors_only'],
        limitations=['controlled-offline', 'process-local', 'trusted-operator', 'validation-only-H1',
            'sequential-authorization', 'cooperative-budgets', 'whole-publication-physical-read',
            'full-history-dtype-admission', 'standalone-configuration-digest-P2-deferred',
            'test-report-not-durable-audit-or-attestation', 'not-production-approval'])
    s['guard']._reports.append((report, s['guard']))
    return report


def assert_public_report(report):
    """Explicit public schema; reject before serializing or writing any evidence."""
    def require(condition, category):
        if not condition:
            # Never include a rejected key, value, repr or exception in diagnostics.
            raise AssertionError('public report '+category)

    # Defense in depth: inspect BOTH dictionary keys and values. Exact builtin
    # types exclude private objects and subclasses with serialization/equality hooks.
    pending = [(report, frozenset())]
    visited = 0
    while pending:
        item, ancestors = pending.pop()
        visited += 1
        require(visited <= 128*1024, 'size')
        item_type = type(item)
        require(any(item_type is allowed for allowed in (dict, list, str, int, bool, type(None))), 'value type')
        if item_type is dict or item_type is list:
            require(id(item) not in ancestors, 'cyclic value')
            lineage = ancestors | {id(item)}
            if type(item) is dict:
                require(all(type(key) is str for key in item), 'dictionary key type')
                pending.extend((key, lineage) for key in item)
                pending.extend((value, lineage) for value in item.values())
            else:
                pending.extend((value, lineage) for value in item)
        elif type(item) is str:
            require('D3D_SECRET' not in item, 'secret sentinel')

    def shape(value, keys, category):
        require(type(value) is dict and set(value) == set(keys), category+' schema')

    def text(value, category):
        require(type(value) is str and bool(value), category+' schema')

    def digest(value, size, category):
        text(value, category)
        require(len(value) == size and set(value) <= set('0123456789abcdef'), category+' schema')

    def timestamp(value, category):
        text(value, category)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise AssertionError('public report '+category+' schema') from None
        require(parsed.tzinfo is not None and parsed.isoformat() == value, category+' schema')

    def ratio(value):
        shape(value, ('numerator', 'denominator'), 'ratio')
        for component in value.values():
            text(component, 'ratio')
        try:
            rational = Fraction(int(value['numerator']), int(value['denominator']))
            normalized = dict(numerator=str(rational.numerator), denominator=str(rational.denominator))
        except (ValueError, ZeroDivisionError):
            raise AssertionError('public report ratio schema') from None
        require(value == normalized, 'ratio schema')

    def statistics(value):
        shape(value, ('count','sae','sse','saa','mae','mse','rmse_radicand','wape_status','wape'), 'statistics')
        require(type(value['count']) is int and value['count'] > 0, 'statistics schema')
        require(value['wape_status'] in ('defined', 'undefined_zero_denominator'), 'statistics schema')
        for name in ('sae','sse','saa','mae','mse','rmse_radicand'):
            ratio(value[name])
        if value['wape_status'] == 'defined':
            ratio(value['wape'])
        else:
            require(value['wape'] is None, 'statistics schema')

    shape(report, ('schema_version','source','fixtures','runtime','tests','semantic',
                   'run','lifecycle','checks','diagnostics','limitations'), 'root')
    require(report['schema_version'] == 'd3d-test-evidence-v1', 'version schema')
    shape(report['source'], ('commit','parent','accepted_baseline','tracked_source_clean'), 'source')
    for name in ('commit','parent','accepted_baseline'):
        digest(report['source'][name], 40, 'source')
    require(report['source']['accepted_baseline'] == BASELINE, 'source schema')
    require(type(report['source']['tracked_source_clean']) is bool, 'source schema')
    shape(report['fixtures'], ('scenario_sha256','mapping_sha256','frozen_sha256'), 'fixtures')
    for value in report['fixtures'].values():
        digest(value, 64, 'fixtures')
    require(report['fixtures']['frozen_sha256'] == FROZEN, 'fixtures schema')
    shape(report['run'], ('candidate_id','eligibility_id','publication_id','capture_id',
        'seal_id','model_handoff_id','baseline_handoff_id','label_revision_ids','label_authority_id',
        'label_authority_version','evaluation_id'), 'run')
    for name in ('candidate_id','eligibility_id','publication_id','capture_id','seal_id',
                 'model_handoff_id','baseline_handoff_id','evaluation_id'):
        digest(report['run'][name], 64, 'run')
    revisions = report['run']['label_revision_ids']
    require(type(revisions) is list and bool(revisions), 'run schema')
    for revision in revisions:
        digest(revision, 64, 'run')
    for name in ('label_authority_id','label_authority_version'):
        text(report['run'][name], 'run')
    shape(report['runtime'], ('python','pytest','xgboost','numpy','pandas','pydantic','pyarrow','scipy'), 'runtime')
    for value in report['runtime'].values():
        text(value, 'runtime')
    require(type(report['tests']) is list and bool(report['tests']), 'tests schema')
    for test in report['tests']:
        shape(test, ('nodeid','outcome'), 'tests')
        text(test['nodeid'], 'tests')
        require(test['outcome'] == 'passed_assertions', 'tests schema')
    shape(report['semantic'], ('scope','baseline_algorithm','groups'), 'semantic')
    require(report['semantic']['scope'] == 'validation_only_h1', 'semantic schema')
    require(report['semantic']['baseline_algorithm'] in ('last_observation_v1','seasonal_naive_v1'), 'semantic schema')
    require(type(report['semantic']['groups']) is list and bool(report['semantic']['groups']), 'groups schema')
    for group in report['semantic']['groups']:
        shape(group, ('origin','series','model','baseline','deltas'), 'groups')
        timestamp(group['origin'], 'groups')
        text(group['series'], 'groups')
        try:
            series = str(UUID(group['series']))
        except ValueError:
            raise AssertionError('public report groups schema') from None
        require(group['series'] == series, 'groups schema')
        statistics(group['model'])
        statistics(group['baseline'])
        shape(group['deltas'], ('sae','sse','mae','mse','wape'), 'deltas')
        for name, value in group['deltas'].items():
            if name == 'wape' and value is None:
                require(group['model']['wape_status'] == group['baseline']['wape_status'] ==
                        'undefined_zero_denominator', 'deltas schema')
            else:
                ratio(value)
    shape(report['checks'], ('training_only','genuine_native','original_owner','claim_before_labels',
        'one_history_capture','one_label_capture','independent_exact_scores','supported_deltas',
        'no_postseal_producers','replay_rejected','continued_c0_verification','stable_serialization'), 'checks')
    require(all(type(v) is bool and v for v in report['checks'].values()), 'checks schema')
    require(type(report['diagnostics']) is list and
            report['diagnostics'] == ['guarded_runtime_errors_only'], 'diagnostics schema')
    limitations = ('controlled-offline','process-local','trusted-operator','validation-only-H1',
        'sequential-authorization','cooperative-budgets','whole-publication-physical-read',
        'full-history-dtype-admission','standalone-configuration-digest-P2-deferred',
        'test-report-not-durable-audit-or-attestation','not-production-approval')
    require(type(report['limitations']) is list and
            all(type(v) is str for v in report['limitations']) and
            len(report['limitations']) == len(limitations) and
            set(report['limitations']) == set(limitations), 'limitations schema')
    require(type(report['lifecycle']) is list and bool(report['lifecycle']), 'lifecycle schema')
    null_events = {'state_fit':'training','native_train':'training','native_load':'c0',
        'native_predict':'c0','baseline_execute':'c0','relationship_approved':'setup',
        'both_handoffs':'sealed','revision':'c1'}
    decision_stages = {
        ('c0','baseline'): {'execute','origin','scoped_return','verify'},
        ('c1','baseline'): {'scoped_return','verify'},
        ('c0','joint'): {'execute','issue','model_bootstrap','model_capture','model_capture_after_read',
            'model_capture_read','model_issue','model_joint_scoped_return','model_joint_verify',
            'model_load','model_origin','model_predict','model_return','model_scoped_return','scoped_return','verify'},
        ('c1','joint'): {'model_joint_scoped_return','model_joint_verify','scoped_return','verify'},
        ('c0','prediction'): {'bootstrap','capture','capture_after_read','capture_read','issue',
            'joint_scoped_return','joint_verify','load','origin','predict','return','scoped_return'},
        ('c1','prediction'): {'joint_scoped_return','joint_verify'},
        ('c1','label'): {'issue','read','after_read','return','scoped_return'},
    }
    for event in report['lifecycle']:
        require(type(event) is list and len(event) == 3, 'lifecycle schema')
        phase, name, detail = event
        require(type(phase) is type(name) is str and phase in ('setup','training','c0','sealed','c1'), 'lifecycle schema')
        if name in null_events:
            require(phase == null_events[name] and detail is None, 'lifecycle schema')
        elif name == 'training_origin':
            require(phase == 'training', 'lifecycle schema')
            timestamp(detail, 'lifecycle')
        elif name in ('read_open','read_close'):
            purposes = {'setup':('production_training','offline_forecast_evaluation'),
                'training':('production_training',),'c0':('offline_forecast_evaluation',),
                'c1':('offline_forecast_evaluation',)}
            require(type(detail) is str and detail in purposes.get(phase, ()), 'lifecycle schema')
        else:
            require(type(detail) is str and detail in decision_stages.get((phase,name), ()), 'lifecycle schema')
    require(len(canonical(report).encode())+1 <= 128*1024, 'size')


def write_public_report(report, path, *, guard):
    """Publish only with the report's original live guard, checked at the write."""
    if type(guard) is not NativeGuard:
        raise AssertionError('acceptance evidence native guard required')
    assert_public_report(report)
    if not any(bound is report and owner is guard for bound, owner in guard._reports):
        raise AssertionError('acceptance evidence guard mismatch')
    encoded = canonical(report)+'\n'
    guard.assert_clean()
    path.write_text(encoded)


@pytest.fixture(scope='module')
def valid_evidence(tmp_path_factory, request):
    """Retain the original report/guard pair privately from a checked native run."""
    path = tmp_path_factory.mktemp('valid-public-report')
    captured = []
    with pytest.MonkeyPatch.context() as patch:
        s = scenario.__wrapped__(path, patch, request)
        original = write_public_report
        def capture(report, target, *, guard):
            original(report, target, guard=guard)
            captured.append(report)
        patch.setitem(globals(), 'write_public_report', capture)
        test_fresh_native_acceptance(s, 'last_observation_v1', request)
    assert len(captured) == 1
    return dict(report=captured[0], guard=s['guard'])


@pytest.fixture(scope='module')
def valid_public_report(valid_evidence):
    return valid_evidence['report']


def test_public_report_rejects_original_secret_key(valid_public_report, valid_evidence, tmp_path):
    bad = copy.deepcopy(valid_public_report)
    bad['diagnostics'] = [
        {'D3D_SECRET_SYNTHETIC_KEY': 'ordinary'}
    ]
    with pytest.raises(AssertionError, match='^public report secret sentinel$'):
        assert_public_report(bad)
    path = tmp_path/'acceptance-malformed.json'
    with pytest.raises(AssertionError, match='^public report secret sentinel$'):
        write_public_report(bad, path, guard=valid_evidence['guard'])
    assert not path.exists()


@pytest.mark.parametrize(('fault','category'), [
    ('secret_value', 'secret sentinel'),
    ('diagnostics_nested', 'diagnostics schema'),
    ('diagnostics_unapproved', 'diagnostics schema'),
    ('limitations_nested', 'limitations schema'),
    ('limitations_unapproved', 'limitations schema'),
    ('lifecycle_type', 'lifecycle schema'),
    ('lifecycle_extra_field', 'lifecycle schema'),
    ('lifecycle_detail_type', 'lifecycle schema'),
    ('lifecycle_unknown_name', 'lifecycle schema'),
    ('lifecycle_wrong_purpose', 'lifecycle schema'),
    ('private_object', 'value type'),
    ('private_metaclass', 'value type'),
    ('unsupported_scalar', 'value type'),
    ('top_key', 'root schema'),
    ('nested_key', 'runtime schema'),
    ('ratio_key', 'ratio schema'),
    ('count_type', 'statistics schema'),
    ('non_string_key', 'dictionary key type'),
    ('cyclic', 'cyclic value'),
])
def test_public_report_rejects_malformed_evidence(valid_public_report, valid_evidence, tmp_path, fault, category):
    bad = copy.deepcopy(valid_public_report)
    if fault == 'secret_value':
        bad['run']['label_authority_id'] = 'D3D_SECRET_SYNTHETIC_VALUE'
    elif fault == 'diagnostics_nested':
        bad['diagnostics'] = [{'ordinary': 'ordinary'}]
    elif fault == 'diagnostics_unapproved':
        bad['diagnostics'] = ['unapproved']
    elif fault == 'limitations_nested':
        bad['limitations'] = [['controlled-offline']]
    elif fault == 'limitations_unapproved':
        bad['limitations'][0] = 'unapproved'
    elif fault == 'lifecycle_type':
        bad['lifecycle'][0] = dict(phase='setup', event='read_open', detail='production_training')
    elif fault == 'lifecycle_extra_field':
        bad['lifecycle'][0].append('unexpected')
    elif fault == 'lifecycle_detail_type':
        bad['lifecycle'][0] = ['setup', 'read_open', 1]
    elif fault == 'lifecycle_unknown_name':
        bad['lifecycle'][0] = ['setup', 'unapproved', None]
    elif fault == 'lifecycle_wrong_purpose':
        bad['lifecycle'][0] = ['training', 'read_open', 'offline_forecast_evaluation']
    elif fault == 'private_object':
        bad['run']['label_authority_id'] = object()
    elif fault == 'private_metaclass':
        class PrivateType(type):
            def __eq__(cls, other):
                raise RuntimeError('private type comparison attempted')
        class Private(metaclass=PrivateType):
            pass
        bad['run']['label_authority_id'] = Private()
    elif fault == 'unsupported_scalar':
        bad['runtime']['python'] = float('nan')
    elif fault == 'top_key':
        bad['unapproved'] = 'ordinary'
    elif fault == 'nested_key':
        bad['runtime']['unapproved'] = 'ordinary'
    elif fault == 'ratio_key':
        bad['semantic']['groups'][0]['model']['sae']['unapproved'] = 'ordinary'
    elif fault == 'count_type':
        bad['semantic']['groups'][0]['model']['count'] = True
    elif fault == 'non_string_key':
        bad['diagnostics'] = [{1: 'ordinary'}]
    elif fault == 'cyclic':
        bad['diagnostics'].append(bad)
    with pytest.raises(AssertionError, match='^public report '+category+'$'):
        assert_public_report(bad)
    path = tmp_path/'acceptance-malformed.json'
    with pytest.raises(AssertionError, match='^public report '+category+'$'):
        write_public_report(bad, path, guard=valid_evidence['guard'])
    assert not path.exists()


def test_public_report_valid_bytes_unchanged(valid_public_report, valid_evidence, tmp_path):
    before = copy.deepcopy(valid_public_report)
    encoded = canonical(before)+'\n'
    assert_public_report(valid_public_report)
    assert valid_public_report == before
    path = tmp_path/'acceptance-valid.json'
    write_public_report(valid_public_report, path, guard=valid_evidence['guard'])
    assert path.read_bytes() == encoded.encode()
    assert json.loads(path.read_text()) == before


@pytest.mark.parametrize('fault', ['missing', 'foreign', 'copied_guard', 'copied_report', 'fake'])
def test_evidence_writer_requires_associated_guard(valid_evidence, tmp_path, monkeypatch, fault):
    report, guard = valid_evidence['report'], valid_evidence['guard']
    path = tmp_path/'acceptance-unbound.json'
    if fault == 'missing':
        with pytest.raises(TypeError, match='guard'):
            write_public_report(report, path)
    else:
        if fault == 'foreign':
            guard = NativeGuard(monkeypatch)
        elif fault == 'copied_guard':
            guard = copy.copy(guard)
            guard.violations = []
        elif fault == 'copied_report':
            report = copy.deepcopy(report)
            assert_public_report(report)  # Valid JSON alone is not write permission.
        else:
            class Fake:
                def assert_clean(self):
                    raise RuntimeError('unexpected fake guard call')
            guard = Fake()
        category = 'native guard required' if fault == 'fake' else 'guard mismatch'
        with pytest.raises(AssertionError, match='^acceptance evidence '+category+'$'):
            write_public_report(report, path, guard=guard)
    assert not path.exists()


@pytest.mark.parametrize('fault', ['recorded', 'after_construction', 'handled_exception',
                                 'during_validation', 'during_serialization'])
def test_evidence_writer_rechecks_live_guard(scenario, monkeypatch, request, fault):
    """Every injection occurs after the real report was built with a clean guard."""
    s = scenario
    original_writer, original_validator, original_canonical = write_public_report, assert_public_report, canonical
    observed, encodings = [], []
    def intercepted(report, path, *, guard):
        assert guard is s['guard'] and not guard.violations and guard.sealed
        assert any(bound is report and owner is guard for bound, owner in guard._reports)
        observed.append(True)
        def trip():
            with pytest.raises(AssertionError, match='^post-seal native operation attempted$'):
                guard.check('controlled-late-publication')
        if fault == 'recorded':
            guard.violations.append('controlled-late-publication')
        elif fault == 'after_construction':
            trip()
        elif fault == 'handled_exception':
            try:
                guard.check('controlled-late-publication')
            except AssertionError:
                pass
        elif fault == 'during_validation':
            def validate(value):
                original_validator(value)
                trip()
            monkeypatch.setitem(globals(), 'assert_public_report', validate)
        else:
            def encode(value):
                encoded = original_canonical(value)
                if value is report:
                    encodings.append(True)
                    if len(encodings) == 2:  # Validator size check, then writer encoding.
                        trip()
                return encoded
            monkeypatch.setitem(globals(), 'canonical', encode)
        original_writer(report, path, guard=guard)
    monkeypatch.setitem(globals(), 'write_public_report', intercepted)
    with pytest.raises(AssertionError, match='post-seal native operation attempted'):
        test_fresh_native_acceptance(s, 'last_observation_v1', request)
    assert observed == [True]
    assert s['guard'].violations == ['controlled-late-publication']
    assert not list(s['tmp'].glob('acceptance-*.json'))
    if fault == 'during_serialization':
        assert encodings == [True, True]


@pytest.mark.parametrize('fault', ['diagnostics', 'limitations'])
def test_positive_scenario_rejects_malformed_evidence(scenario, monkeypatch, request, fault):
    """Drive a malformed report through the positive scenario's real writer."""
    original = build_report
    injected = []
    def malformed(*args, **kwargs):
        report = original(*args, **kwargs)
        report[fault] = ([{'D3D_SECRET_SYNTHETIC_KEY': 'ordinary'}]
                         if fault == 'diagnostics' else [['controlled-offline']])
        injected.append(fault)
        return report
    monkeypatch.setitem(globals(), 'build_report', malformed)
    category = 'secret sentinel' if fault == 'diagnostics' else 'limitations schema'
    with pytest.raises(AssertionError, match='^public report '+category+'$'):
        test_fresh_native_acceptance(scenario, 'last_observation_v1', request)
    assert injected == [fault]
    assert not list(scenario['tmp'].glob('acceptance-*.json'))


@pytest.mark.parametrize('fault', ['cancel', 'expiry', 'revoke', 'relationship', 'revision', 'cleanup',
                                 'late_scope','cancel_read','revoke_after_read','expiry_return','prediction'])
def test_fresh_terminal_failures(scenario, fault, capsys, caplog):
    s = scenario
    b = s['seal']('last_observation_v1')
    h = b['label_op'].issue()
    if fault == 'cancel':
        b['label_op'].cancel(h)
    elif fault == 'revoke':
        b['label_op'].revoke(h)
    elif fault == 'expiry':
        s['clock'][0] = 10.  # Label expiry equality; C0/prediction and B0 remain live.
        assert b['verifier'].verify(b['session']).result_id == b['sealed'].result_id
    elif fault == 'prediction':
        b['op'].cancel(b['session'])
    elif fault == 'relationship':
        s['bo'].revoke(s['approval'])
    elif fault == 'revision':
        s['control']['revision'] = False
    elif fault == 'late_scope':
        s['control']['deny'] = ('label', 'scoped_return')
    elif fault in ('cancel_read','revoke_after_read','expiry_return'):
        target = {'cancel_read':'read','revoke_after_read':'after_read','expiry_return':'return'}[fault]
        def hook(kind, stage):
            if (kind,stage) != ('label',target):
                return
            if fault == 'cancel_read':b['label_op'].cancel(h)
            elif fault == 'revoke_after_read':b['label_op'].revoke(h)
            else:s['clock'][0] = 10.
        s['control']['hook'] = hook
    else:
        read = s['provider'].open_read
        @contextmanager
        def cleanup(*a, **kw):
            with read(*a, **kw) as session:
                yield session
            raise RuntimeError('D3D_SECRET_CLEANUP_SENTINEL')
        s['provider'].open_read = cleanup
    published = []
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(ModelPredictionError) as caught:
            published.append(b['evaluator'].evaluate(h))
    assert published == []
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    assert 'D3D_SECRET' not in (str(error)+repr(error)+repr(error.args)+''.join(traceback.format_exception(error))
                               +repr(seen)+capsys.readouterr().err+caplog.text)
    with pytest.raises(ModelPredictionError):
        b['evaluator'].evaluate(h)
    captures = sum(p == 'c1' and e == 'read_open' for p, e, _ in s['trace'])
    assert captures == (1 if fault in ('revision', 'cleanup', 'late_scope','revoke_after_read','expiry_return') else 0)


def test_fresh_concurrent_consumers(scenario):
    s = scenario
    b = s['seal']('last_observation_v1')
    op2, evaluator2 = b['verifier'].bootstrap_evaluation(b['session'], **b['composition'])
    h1, h2 = b['label_op'].issue(), op2.issue()
    def consume(evaluator, handle):
        try:
            return evaluator.evaluate(handle)
        except ModelPredictionError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume, e, h) for e, h in ((b['evaluator'], h1), (evaluator2, h2))]
        results = [f.result() for f in futures]
    assert sum(r is not None for r in results) == 1
    assert sum(p == 'c1' and e == 'read_open' for p, e, _ in s['trace']) == 1


@pytest.mark.parametrize('scenario',['zero_truth'],indirect=True)
def test_native_zero_truth_preserves_undefined_wape(scenario):
    s = scenario
    b = s['seal']('last_observation_v1')
    result = b['evaluator'].evaluate(b['label_op'].issue())
    check_scores(s, result)
    for scores in (result.model_scores,result.baseline_scores):
        for group in scores.groups:
            assert group.statistics.saa.fraction == 0
            assert group.statistics.wape is None
            assert group.statistics.wape_status == 'undefined_zero_denominator'
    assert all(d.wape is None and d.wape_status == 'undefined_zero_denominator' for d in result.deltas)


def test_independent_runs_have_equal_semantics(tmp_path, monkeypatch, request):
    reports = []
    for name in ('first','second'):
        with monkeypatch.context() as patch:
            s = scenario.__wrapped__(tmp_path/name, patch, request)
            b = s['seal']('last_observation_v1')
            result = b['evaluator'].evaluate(b['label_op'].issue())
            semantic = check_scores(s, result)
            report = build_report(s, result, semantic, request.node.nodeid)
            assert_public_report(report)
            reports.append(report)
    assert canonical(reports[0]['semantic']) == canonical(reports[1]['semantic'])
    assert reports[0]['fixtures'] == reports[1]['fixtures']
    assert reports[0]['run']['seal_id'] != reports[1]['run']['seal_id']


@pytest.mark.parametrize('fault',['group_identity','group_membership','delta_identity','delta_membership',
                                'inplace_predict','predict','load_model','update'])
def test_injected_fault_cannot_publish_affirmative_evidence(tmp_path, monkeypatch, request, fault):
    """Exercise the positive test itself, including its actual evidence sink."""
    models=[]
    genuine_train=xgb.train
    genuine_inplace=xgb.Booster.inplace_predict
    genuine_predict=xgb.Booster.predict
    genuine_load=xgb.Booster.load_model
    genuine_update=xgb.Booster.update
    def observed_train(*args,**kwargs):
        model=genuine_train(*args,**kwargs)
        models.append(model)
        return model
    monkeypatch.setattr(xgb,'train',observed_train)
    s=scenario.__wrapped__(tmp_path,monkeypatch,request)
    assert len(models)==1 and type(models[0]) is xgb.Booster
    matrix=xgb.DMatrix(np.zeros((1,3),dtype=np.float32),label=np.zeros(1,dtype=np.float32),
                       feature_names=models[0].feature_names,nthread=1)
    artifact=models[0].save_raw(raw_format='ubj')
    attempted=[];completed=[];publications=[]
    if fault.startswith(('group_','delta_')):
        from src.services.canonical_joint_model_evaluation_service import _EvaluationOwner
        original=_EvaluationOwner.evaluate
        def corrupted(owner,handle):
            result=original(owner,handle)
            if fault.startswith('group_'):
                groups=list(result.model_scores.groups)
                if fault=='group_identity':
                    groups[0]=groups[0].model_copy(update={'series_id':groups[1].series_id})
                else:
                    first,second=groups[0].event_ids,groups[1].event_ids
                    groups[0]=groups[0].model_copy(update={'event_ids':second})
                    groups[1]=groups[1].model_copy(update={'event_ids':first})
                scores=result.model_scores.model_copy(update={'groups':tuple(groups)})
                mutant=result.model_copy(update={'model_scores':scores})
            else:
                deltas=list(result.deltas)
                update=({'semantic_group_id':'0'*64} if fault=='delta_identity'
                        else {'forecast_key_ids':('0'*64,)})
                deltas[0]=deltas[0].model_copy(update=update)
                mutant=result.model_copy(update={'deltas':tuple(deltas)})
            # Full public validation succeeds: the independent acceptance oracle
            # must reject this semantic substitution, not outsource its oracle.
            mutant=type(result).model_validate_json(mutant.model_dump_json())
            attempted.append(fault)
            return mutant
        monkeypatch.setattr(_EvaluationOwner,'evaluate',corrupted)
    else:
        # Positive pre-seal control uses the saved genuine Booster method.
        assert genuine_inplace(models[0],np.zeros((1,3),dtype=np.float32)).shape==(1,)
        calls=dict(inplace_predict=lambda:genuine_inplace(models[0],np.zeros((1,3),dtype=np.float32)),
                   predict=lambda:genuine_predict(models[0],matrix),
                   load_model=lambda:genuine_load(models[0],artifact),
                   update=lambda:genuine_update(models[0],matrix,0))
        def inject(kind,stage):
            if (kind,stage)==('label','read'):
                attempted.append(fault)
                try:
                    calls[fault]()
                    completed.append(fault)
                except AssertionError:
                    # A collaborator may swallow the guard's exception. The
                    # persistent violation latch must still prevent evidence.
                    pass
        s['control']['hook']=inject
    original_report=build_report
    def published(*args,**kwargs):
        publications.append(True)
        return original_report(*args,**kwargs)
    monkeypatch.setitem(globals(),'build_report',published)
    with pytest.raises(AssertionError):
        test_fresh_native_acceptance(s,'last_observation_v1',request)
    assert attempted==[fault]
    assert not completed
    assert not publications
    assert not list(tmp_path.glob('acceptance-*.json'))
    if not fault.startswith(('group_','delta_')):
        assert len(s['guard'].violations)==1
        assert s['guard'].violations[0].startswith('C.XGBooster')
