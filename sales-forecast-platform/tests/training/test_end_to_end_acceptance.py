"""D3-D: fresh, process-local offline acceptance; no production integrations.

The accepted D2 test factory supplies admitted training data and its independent
release ledger. Evaluation ingestion, approval and C0/C1 composition are explicit
here. Instrumentation is installed before invoking even that training factory.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
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
    assert_public_report(report)
    path = s['tmp']/('acceptance-'+algorithm+'.json')
    path.write_text(canonical(report)+'\n')
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
    return dict(schema_version='d3d-test-evidence-v1',
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


def assert_public_report(report):
    assert set(report) == {'schema_version','source','fixtures','runtime','tests','semantic',
                           'run','lifecycle','checks','diagnostics','limitations'}
    assert set(report['source']) == {'commit','parent','accepted_baseline','tracked_source_clean'}
    assert set(report['fixtures']) == {'scenario_sha256','mapping_sha256','frozen_sha256'}
    assert set(report['run']) == {'candidate_id','eligibility_id','publication_id','capture_id',
        'seal_id','model_handoff_id','baseline_handoff_id','label_revision_ids','label_authority_id',
        'label_authority_version','evaluation_id'}
    assert set(report['semantic']) == {'scope','baseline_algorithm','groups'}
    assert set(report['runtime']) == {'python','pytest','xgboost','numpy','pandas','pydantic','pyarrow','scipy'}
    assert all(set(test) == {'nodeid','outcome'} and test['outcome'] == 'passed_assertions'
               for test in report['tests'])
    stats = {'count','sae','sse','saa','mae','mse','rmse_radicand','wape_status','wape'}
    for group in report['semantic']['groups']:
        assert set(group) == {'origin','series','model','baseline','deltas'}
        assert set(group['model']) == set(group['baseline']) == stats
        assert set(group['deltas']) == {'sae','sse','mae','mse','wape'}
        for values in (group['model'],group['baseline'],group['deltas']):
            for name,value in values.items():
                if name in ('count','wape_status') or value is None:
                    continue
                assert set(value) == {'numerator','denominator'}
                rational = Fraction(int(value['numerator']),int(value['denominator']))
                assert value == {'numerator':str(rational.numerator),'denominator':str(rational.denominator)}
    assert set(report['checks']) == {'training_only','genuine_native','original_owner','claim_before_labels',
        'one_history_capture','one_label_capture','independent_exact_scores','supported_deltas',
        'no_postseal_producers','replay_rejected','continued_c0_verification','stable_serialization'}
    assert all(type(v) is bool and v for v in report['checks'].values())
    assert all(len(event) == 3 and event[0] in ('setup','training','c0','sealed','c1')
               for event in report['lifecycle'])
    pending = [report]
    forbidden = {'manifest','labels','state_json','artifact_base64','matrix_float32_hex',
                 'predictions_float32_hex','payload','credentials','password','handle','session'}
    while pending:
        item = pending.pop()
        assert type(item) in (dict,list,str,int,bool,type(None))
        if type(item) is dict:
            assert not set(item) & forbidden
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif type(item) is str:
            assert 'D3D_SECRET' not in item
    assert len(canonical(report).encode()) <= 128*1024


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
