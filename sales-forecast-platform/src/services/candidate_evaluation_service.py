"""M8 trusted offline TEST_ONLY evaluation; no estimator, registry or runtime API."""
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from src.data.contracts.backtesting import identity
from src.data.contracts.dataset import operational_json
from src.data.contracts.candidate_evaluation import (
    CandidateEvaluationRequest,CandidateEvidence,EnsembleResult,EnsemblePoint,MultiCandidateComparison,SeriesMetric)
from src.data.processing.candidate_evaluation import metrics,ensemble_values,rational,POLICY,signed
from src.providers.candidate_evaluation_provider import CandidateEvaluationProvider
from src.services.candidate_packaging_service import CandidatePackagingService
from src.services.canonical_adapter_training_service import CandidateReference
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline


class CandidateEvaluationError(ValueError):pass


def _reject():
    error=CandidateEvaluationError('candidate evaluation rejected')
    try:raise error from None
    finally:error.__cause__=None;error.__context__=None


def evidence(manifest):
    """Non-authoritative declaration. Service compares with original-owner bytes."""
    try:
        from src.data.contracts.candidate_package import CandidatePackageManifest
        if type(manifest) is not CandidatePackageManifest:raise ValueError()
        manifest=CandidatePackageManifest.model_validate_json(manifest.canonical_json)
        return CandidateEvidence(candidate_id=manifest.candidate_id,family=manifest.training.specification.family,
            package_id=manifest.package_id,representation_binding_id=manifest.representation_binding_id,
            representation_compatibility_id=manifest.training.representation_compatibility_id,
            fitted_state_id=manifest.preprocessing.state_id,preprocessing_schema_id=manifest.preprocessing.schema_id)
    except Exception:pass
    _reject()


@dataclass(frozen=True)
class _Binding:
    packaging: object
    candidate: object
    package_id: str


class CandidateEvaluationService:
    def __init__(self,provider,candidates,*,authorize_evaluation,elapsed_clock=monotonic):
        # Constructor is trusted host bootstrap, NEVER untrusted request ingress.
        self._bindings={};self._provider=provider;self._authorize=authorize_evaluation;self._clock=elapsed_clock
        self._self=self;self._lock=Lock()
        try:
            if (type(provider) is not CandidateEvaluationProvider or type(candidates) is not tuple or not 2<=len(candidates)<=8
                    or not callable(authorize_evaluation) or not callable(elapsed_clock)):raise ValueError()
            for packaging,candidate,package_id in candidates:
                if type(packaging) is not CandidatePackagingService or type(candidate) is not CandidateReference:raise ValueError()
                m=packaging.verify(package_id,candidate=candidate)
                if m.candidate_id in self._bindings:raise ValueError()
                self._bindings[m.candidate_id]=_Binding(packaging,candidate,package_id)
            return
        except Exception:pass
        _reject()

    def _owned(self,request,checkpoint):
        if self._self is not self:raise ValueError()
        manifests=[];engine=None
        for batch in request.batches:
            checkpoint();bound=self._bindings.get(batch.source.candidate_id)
            if bound is None or bound.package_id!=batch.source.package_id:raise ValueError()
            m=bound.packaging.verify(bound.package_id,candidate=bound.candidate);checkpoint()
            if evidence(m)!=batch.source or m.plan!=request.plan:raise ValueError()
            # Read only the original owner's private frozen configuration; never
            # inspect/call the private model or export raw state.
            record,_=bound.packaging._owned(bound.candidate)
            state=record[4]
            base=state if m.training.capability.representation=='tabular' else state.encoder.base if batch.source.family=='transformer' else state.base
            current=CanonicalFeaturePipeline(base.canonical_schema.configuration)
            if engine is not None and current.configuration.target_metric!=engine.configuration.target_metric:raise ValueError()
            engine=current;manifests.append(m)
        return engine,tuple(manifests)

    def _admit(self,request,engine):
        plan=request.plan;train=plan.datasets[0];ev=request.evaluation;fold=plan.folds[0]
        if (ev.reference==train.reference or ev.reference.organization_id!=train.reference.organization_id
                or ev.reference.dataset_id!=train.reference.dataset_id or ev.mapping_digest!=train.mapping_digest
                or ev.source_id!=train.source_id or ev.series_ids!=train.series_ids
                or ev.series_definition_digest!=train.series_definition_digest):raise ValueError()
        origins=getattr(fold,request.partition+'_origins')
        eligible={(str(s),o.isoformat()) for s in train.series_ids for o in origins}
        chosen=set();cfg=engine.configuration;measurement=cfg.execution.measurement
        for key in request.keys:
            s=key.scope;projection=(str(s.series_id),s.forecast_origin.isoformat())
            if (projection not in eligible or projection in chosen or s.plan_digest!=plan.digest
                    or s.dataset_version!=ev.reference.version_id or s.fold_id!=fold.fold_id
                    or s.organization_id!=ev.reference.organization_id or s.metric!=cfg.target_metric
                    or (s.unit,s.currency)!=(measurement.unit,measurement.currency)
                    or (s.frequency,s.timezone)!=(plan.frequency,plan.timezone)
                    or engine._time(s.target_period_end)!=engine._advance(engine._time(s.forecast_origin),1)
                    or engine._time(fold.model_fit_cutoff)>engine._time(s.forecast_origin)):raise ValueError()
            chosen.add(projection)
        if request.coverage=='exact_grid_v1' and chosen!=eligible:raise ValueError()
        if len(request.batches)*len(request.keys)*32>request.resources.max_work:raise ValueError()
        if (len({k.scope.series_id for k in request.keys})*(len(request.batches)+1)>request.resources.max_groups):raise ValueError()
        return len(eligible)

    def evaluate(self,request,*,cancelled=lambda:False):
        if not self._lock.acquire(blocking=False):_reject()
        try:
            if type(request) is CandidateEvaluationRequest:
                req=CandidateEvaluationRequest.model_validate_json(request.canonical_json)
            elif type(request) is dict:
                req=CandidateEvaluationRequest.model_validate_json(operational_json(request))
            elif type(request) in (str,bytes):
                req=CandidateEvaluationRequest.model_validate_json(request)
            else:raise ValueError()
            start=self._clock()
            if type(start) not in (float,int) or type(start) is bool:raise ValueError()
            def check():
                stop=cancelled();now=self._clock()
                import math
                if (type(now) not in (float,int) or type(now) is bool or not math.isfinite(now)
                        or not math.isfinite(start) or now<start or now-start>=req.resources.max_elapsed_seconds
                        or stop is not False):raise ValueError()
            def approve(stage):
                check()
                if self._authorize(req,stage) is not True:raise ValueError()
                check()
            check()
            if len(req.canonical_bytes)>1024*1024:raise ValueError()
            engine,manifests=self._owned(req,check)
            eligible=self._admit(req,engine);approve('admit')
            # Immutable request/config/predictions are fully validated before
            # any truth access. No prediction callback exists in this module.
            used={'captures':0,'rows':0,'decoded':0,'intermediate':len(req.canonical_bytes),'work':0}
            limits={'captures':1,'rows':req.resources.max_capture_rows,'decoded':req.resources.max_decoded_bytes,
                    'intermediate':req.resources.max_intermediate_bytes,'work':req.resources.max_work}
            def charge(kind,n):
                check()
                if kind not in used or type(n) is not int or n<0:raise ValueError()
                used[kind]+=n
                if used[kind]>limits[kind]:raise ValueError()
            truth,metadata=self._provider._capture(req,engine,check,charge)
            truths={t.key.key_id:t.value for t in truth}
            if set(truths)!={k.key_id for k in req.keys}:raise ValueError()
            revision=identity([t.revision_claim_id for t in truth]);population=identity([[k.key_id for k in req.keys],revision])
            scores=[];series_scores=[]
            def score(producer_id,values):
                pairs=[(values[k.key_id],truths[k.key_id]) for k in req.keys]
                scores.append(metrics(producer_id,population,pairs,check))
                for series in sorted({k.scope.series_id for k in req.keys},key=str):
                    keys=tuple(k for k in req.keys if k.scope.series_id==series)
                    group_id=identity([population,str(series),[k.key_id for k in keys]])
                    series_scores.append(SeriesMetric(series_id=series,metric=metrics(producer_id,group_id,
                        [(values[k.key_id],truths[k.key_id]) for k in keys],check)))
            for batch in req.batches:
                score(batch.source.candidate_id,{p.key.key_id:rational(p.value,POLICY) for p in batch.observations})
            model_scores=tuple(scores)
            ensemble=ensemble_values(req.ensemble,req.batches,req.keys,check)
            score(req.ensemble.configuration_id,{k.key_id:v for k,v in ensemble})
            result=MultiCandidateComparison(request_id=req.request_id,evaluation=req.evaluation,coverage=req.coverage,
                eligible_count=eligible,evaluated_count=len(req.keys),excluded_count=eligible-len(req.keys),keys=req.keys,
                sources=tuple(b.source for b in req.batches),prediction_batch_ids=tuple(b.batch_id for b in req.batches),
                truth_revision_id=revision,population_id=population,metrics=model_scores,series_metrics=tuple(series_scores),
                ranking=tuple(s.producer_id for s in sorted(model_scores,key=lambda s:(s.statistics.mae.fraction,s.producer_id))),
                ensemble=EnsembleResult(configuration=req.ensemble,points=tuple(EnsemblePoint(key=k,value=signed(v)) for k,v in ensemble),metric=scores[-1]))
            if len(result.canonical_bytes)>req.resources.max_output_bytes:raise ValueError()
            self._owned(req,check);self._provider._fresh(req,metadata,check)
            for key in req.keys:approve('return:'+key.key_id)
            approve('return');self._owned(req,check)
            self._provider._fresh(req,metadata,check);check()
            # Final local owner liveness checks invoke no further authority callback.
            for batch in req.batches:
                bound=self._bindings[batch.source.candidate_id]
                bound.packaging._owned(bound.candidate)
            return result
        except Exception:pass
        finally:self._lock.release()
        _reject()
