"""D3-B trusted same-process H1 execution; no labels, fitting or scoring API.

Only the original live D2 issuer composes this owner. Application bootstrap must
retain the operator and pin the returned executor. Python is not a sandbox.
Publication/authority checks are sequential, not atomic leases. Clocks/providers
are trusted bootstrap collaborators, never request parameters. Native calls are
indivisible; cancellation and resource ceilings are cooperative, not OS limits.
"""
from dataclasses import dataclass
import math
from pathlib import Path
import struct
import subprocess
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

import numpy as np
import pandas as pd

from src.data.contracts.backtesting import ForecastScope, PredictionPoint, identity
from src.data.contracts.dataset import StagingBudgets
from src.data.contracts.forecast import ForecastConfig
from src.data.contracts.model_candidate import strict_float32, digest
from src.data.contracts.model_evaluation import ModelEvaluationEligibility
from src.data.contracts.model_prediction import FittedH1Prediction, FittedH1PredictionResult
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline, FeatureBudgets
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility, verify_feature_schema
from src.providers.canonical_training_data_provider import CanonicalTrainingDataProvider
from src.services.canonical_training_preparation_service import (
    CanonicalTrainingPreparationService, PreparationBudgets, TemporalPreparationContext,
    ValueAttestedAvailabilityVerifier)
from src.services.training_evaluation_relationship_authority import RelationshipApprovalVerifier


class ModelPredictionError(ValueError):
    """Fixed diagnostics at all supported prediction/operator entry points."""


def _guard(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception:
        pass
    error=ModelPredictionError('owned validation prediction unavailable or invalid')
    try:raise error from None
    finally:error.__cause__=None;error.__context__=None


def _continue():return False


@dataclass(frozen=True, slots=True)
class EvaluationUseContext:
    eligibility_json: str
    relationship_approval_id: str
    evaluation_use_id: str | None
    forecast_origin: pd.Timestamp | None
    purpose: str = 'offline_forecast_evaluation'


class EvaluationUse:
    """Original process-local object; identifiers and receipts grant nothing."""
    __slots__=()
    def __reduce__(self):raise TypeError('evaluation-use handles are process-local')


@dataclass(slots=True)
class _Use:
    use_id: str
    expires: float
    status: str = 'READY'


def _source_current(runtime):
    root=Path(__file__).resolve().parents[3]
    # D2 pins its own files; additionally pin this new executor and result code.
    for name in ('src/services/canonical_model_prediction_service.py','src/data/contracts/model_prediction.py',
                 'src/services/training_evaluation_relationship_authority.py','src/data/contracts/model_evaluation.py'):
        relative='sales-forecast-platform/'+name
        committed=subprocess.check_output(['git','cat-file','blob',runtime.source_commit+':'+relative],cwd=root,timeout=5)
        if committed!=(root/relative).read_bytes():raise ValueError()


class _Owner:
    def __init__(self, current, material, *, eligibility, provider, relationship_verifier,
                 relationship_approval, authorize_evaluation, verify_history,
                 elapsed_clock=monotonic):
        if (type(eligibility) is not ModelEvaluationEligibility
                or type(provider) is not CanonicalTrainingDataProvider
                or type(relationship_verifier) is not RelationshipApprovalVerifier
                or type(verify_history) is not ValueAttestedAvailabilityVerifier
                or not all(callable(v) for v in (current,authorize_evaluation,elapsed_clock))):raise ValueError()
        checked=ModelEvaluationEligibility.model_validate(eligibility)
        self.declaration=ModelEvaluationEligibility.model_validate_json(checked.model_dump_json(warnings='error'))
        self.encoded=self.declaration.model_dump_json(warnings='error')
        projection,artifact,state_json,schema_json,runtime,attributes,rounds=material
        d=self.declaration
        if d.candidate!=projection or d.runtime.model_dump_json()!=runtime.model_dump_json():raise ValueError()
        self.engine=CanonicalFeaturePipeline(d.candidate.representation.configuration,
            budgets=FeatureBudgets(max_rows=d.resources.max_captured_rows,max_horizon=1,
                max_frame_bytes=d.resources.max_decoded_bytes,max_result_bytes=d.resources.max_representation_bytes))
        # Initial H1 adapter supports history plus verified identity categories.
        # Unsupported covariates reject explicitly, never fall back or refit.
        if self.engine.rules.exogenous or any(r.field not in self.engine.rules.dimension_fields for r in self.engine.rules.categories):
            raise ValueError()
        from src.data.contracts.features import FeatureState
        self.state=self.engine.validate_fitted_state(FeatureState.model_validate_json(state_json))
        self.schema=FeatureSchemaCompatibility.model_validate_json(schema_json)
        if (digest(state_json.encode())!=d.candidate.fitted_state_digest
                or self.state.state_id!=d.candidate.fitted_state_id
                or self.schema!=d.candidate.representation):raise ValueError()
        self.current=current;self.artifact=artifact;self.runtime=runtime
        self.attributes=attributes;self.rounds=rounds;self.provider=provider
        self.relationship=relationship_verifier;self.approval=relationship_approval
        self.authorize=authorize_evaluation
        # Snapshot the trusted callbacks, not a caller-mutable verifier facade.
        self.history=ValueAttestedAvailabilityVerifier(verify_availability=verify_history._availability,
            verify_captured_values=verify_history._values)
        self.clock=elapsed_clock;self.previous=None;self.lock=RLock();self.busy=Lock()
        self.handle=None;self.record=None;self.used=False;self.checks=0;self.approval_id=None
        self.__joint_record=None
        self._admit()
        check=self._checkpoint(None,_continue)
        self._fresh('bootstrap',check)

    def _now(self):
        value=self.clock()
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError()
        with self.lock:
            if self.previous is not None and value<self.previous:raise ValueError()
            self.previous=value
        return value

    def _active(self,record,*,sealed=False):
        with self.lock:
            if record is not None and (self.record is not record or record.status not in (('COMPLETED',) if sealed else ('READY','RUNNING'))):raise ValueError()

    def _checkpoint(self,record,cancelled,*,sealed=False):
        if not callable(cancelled):raise ValueError()
        start=self._now()
        def check():
            stop=cancelled();now=self._now()
            if (type(stop) is not bool or stop or now-start>=self.declaration.resources.max_elapsed_seconds
                    or record is not None and now>=record.expires):raise ValueError()
            self._active(record,sealed=sealed)
        check();return check

    def _admit(self):
        d=self.declaration;b=d.resources
        # Reuse the executor's operation-count policy across the whole request,
        # rather than allowing each individually small origin its own allowance.
        work=sum(c.parameters['window'] if c.operation.startswith('rolling_') else max(1,len(c.inputs))
                 for c in self.engine.configuration.calculations)
        work+=len(self.engine.rules.categories)**2+2*sum(len(v) for v in self.state.vocabularies.values())
        if (len(self.artifact)>b.max_artifact_bytes or digest(self.artifact)!=d.candidate.artifact_digest
                or digest(self.state.model_dump_json().encode())!=d.candidate.fitted_state_digest
                or self.schema.schema_id!=d.capabilities.representation_schema_id
                or self.engine.digest!=d.candidate.configuration_digest
                or len(d.coverage.periods)*len(d.relationship.series)*work>self.engine.budgets.max_calculation_cells
                or len(d.coverage.periods)>b.max_native_calls
                or len(d.coverage.periods)*len(d.relationship.series)*len(d.capabilities.feature_names)*8>b.max_representation_bytes
                or len(d.coverage.periods)*len(d.relationship.series)*len(d.capabilities.feature_names)>b.max_input_cells):raise ValueError()

    def _fresh(self,stage,check,record=None,origin=None,metadata=None,*,sealed=False):
        check();self.checks+=1
        if self.checks>self.declaration.resources.max_verification_calls:raise ValueError()
        self.current(stage);check();_source_current(self.runtime)
        receipt=self.relationship.verify(self.declaration,self.approval,
            cancelled=lambda:(check() or False))
        if self.approval_id is None:self.approval_id=receipt.approval_id
        if receipt.approval_id!=self.approval_id:raise ValueError()
        if metadata is not None:self.provider.require_current(metadata,budgets=self._read_budgets())
        context=EvaluationUseContext(self.encoded,self.approval_id,record.use_id if record else None,origin)
        if self.authorize(context,stage) is not True:raise ValueError()
        self._gate(check,record,sealed=sealed)

    def _gate(self,check,record,*,sealed=False):
        check()
        self.relationship._require_active_for_composition(self.approval,self.declaration.eligibility_id)
        # No collaborator after the B0 liveness check; inspect our own ledger.
        self._active(record,sealed=sealed)

    def _read_budgets(self):
        b=self.declaration.resources
        return StagingBudgets(max_rows=b.max_captured_rows,max_elapsed_seconds=b.max_elapsed_seconds)

    def issue(self,*,cancelled=_continue):
        if not self.busy.acquire(blocking=False):raise ValueError()
        try:
            if self.used:raise ValueError()
            self.used=True;check=self._checkpoint(None,cancelled)
            self._admit();self._fresh('issue',check)
            handle=EvaluationUse();record=_Use(identity(['evaluation_use_v1',self.declaration.eligibility_id,
                self.approval_id,uuid4().hex]),self._now()+self.declaration.resources.max_elapsed_seconds)
            self._gate(check,None)
            with self.lock:self.handle=handle;self.record=record
            return handle
        finally:self.busy.release()

    def stop(self,handle,status):
        with self.lock:
            if type(handle) is not EvaluationUse or handle is not self.handle or self.record.status not in ('READY','RUNNING'):raise ValueError()
            self.record.status=status

    def revoke(self,handle):self.stop(handle,'REVOKED')
    def cancel(self,handle):self.stop(handle,'CANCELLED')

    def predict(self,handle,*,cancelled=_continue):
        return self._run(handle,cancelled,None)

    def _joint_predict(self,shared,handle,*,cancelled=_continue):
        from src.services.canonical_joint_model_prediction_service import _JointExecution
        if type(shared) is not _JointExecution or not shared.permits_model(self):raise ValueError()
        return self._run(handle,cancelled,shared)

    def _run(self,handle,cancelled,shared):
        if not self.busy.acquire(blocking=False):raise ValueError()
        record=None
        try:
            with self.lock:
                if type(handle) is not EvaluationUse or handle is not self.handle or self.record.status!='READY':raise ValueError()
                record=self.record;record.status='RUNNING'
            check=self._checkpoint(record,cancelled)
            return self._execute(record,check,shared)
        finally:
            with self.lock:
                if record is not None and record.status=='RUNNING':record.status='FAILED'
            self.busy.release()

    @staticmethod
    def _load_native(artifact,names,attributes,rounds):
        import xgboost as xgb
        model=xgb.Booster(params={'nthread':1});model.load_model(bytearray(artifact))
        if (tuple(model.feature_names or ())!=names or model.num_features()!=len(names)
                or model.num_boosted_rounds()!=rounds or model.attributes()!=dict(attributes)):raise ValueError()
        return model

    @staticmethod
    def _predict_native(model,matrix,names):
        import xgboost as xgb
        return model.predict(xgb.DMatrix(matrix,feature_names=list(names),nthread=1),validate_features=True)

    def _execute(self,record,check,shared=None):
        d=self.declaration;b=d.resources;engine=self.engine;binding=d.relationship.evaluation
        self._admit();self._fresh('capture',check,record)
        def factory(meta):
            if (CanonicalTrainingPreparationService._temporal_binding(meta,binding.series_ids)!=binding
                    or meta.purpose!='offline_forecast_evaluation'
                    or meta.rows>b.max_captured_rows
                    or meta.rows*len(d.coverage.periods)*128>1_000_000):raise ValueError()
            return self._context(meta,None,None)
        def capture_authorization(context,stage):
            factory(context.metadata)
            ctx=EvaluationUseContext(self.encoded,self.approval_id,record.use_id,None)
            if self.authorize(ctx,'capture_'+stage) is not True:raise ValueError()
            self._gate(check,record)
            return True
        preparation=CanonicalTrainingPreparationService(self.provider,authorize_preparation=capture_authorization,
            verify_availability=self.history,elapsed_clock=self.clock)
        meta,base,frame,definitions=preparation._capture(binding.reference,engine,binding.series_ids,
            'offline_forecast_evaluation',factory,engine.budgets,
            PreparationBudgets(b.max_decoded_bytes,b.max_elapsed_seconds),self._read_budgets(),check)
        if shared is not None:
            frame=shared.capture(self,record,meta,frame,definitions)
        # Full bounded integrity capture is closed now. No scoring-label reader
        # or target-at-origin extraction exists. Only eligible history is used.
        requests=pd.DataFrame([dict(organization_id=meta.reference.organization_id,series_id=s,
            metric=d.relationship.metric,unit=d.relationship.unit,currency=d.relationship.currency,
            frequency=d.relationship.frequency,**definitions[s].dimensions) for s in binding.series_ids])
        self._fresh('load',check,record,metadata=meta)
        model=self._load_native(self.artifact,d.capabilities.feature_names,self.attributes,self.rounds)
        check();events=[];terminal=[];representation_bytes=0;evidence_bytes=0;output_bytes=0
        for period in d.coverage.periods:
            origin=engine._time(period.forecast_origin);end=engine._time(period.target_period_end)
            self._fresh('origin',check,record,origin,meta)
            history=frame[(frame.period_end<=origin)&(frame.available_at<=origin)].copy()
            if history.empty:raise ValueError()
            claims,exo=preparation._availability(history,engine,check)
            evidence_bytes+=sum(len(repr(c).encode()) for c in claims)
            if evidence_bytes>b.max_decoded_bytes:raise ValueError()
            ctx=self._context(meta,origin,end)
            if self.history(ctx,claims) is not True:raise ValueError()
            forecast=ForecastConfig(forecast_frequency=d.relationship.frequency,forecast_horizon=1)
            matrix=engine.prepare_inference(history,requests,self.state,forecast_origin=origin,forecast_config=forecast)
            verify_feature_schema(self.schema,engine.configuration,self.state,matrix,requests=requests,
                forecast_config=forecast,forecast_origin=origin)
            if tuple(matrix.columns)!=d.capabilities.feature_names:raise ValueError()
            representation_bytes+=int(matrix.memory_usage(deep=True).sum())
            if representation_bytes>b.max_representation_bytes:raise ValueError()
            # Read typed scalars before a mixed-column ndarray can erase bool
            # identity. The approved input-cell budget bounds this detached copy.
            raw=tuple(tuple(matrix.iat[i,j] for j in range(len(matrix.columns))) for i in range(len(matrix)))
            x=strict_float32(raw)
            history_id=identity([d.eligibility_id,origin.isoformat(),[repr(c) for c in claims]])
            self._fresh('predict',check,record,origin,meta)
            if self.history(ctx,claims) is not True:raise ValueError()
            self._gate(check,record)  # Last checked authority boundary before native predict.
            values=self._predict_native(model,x,d.capabilities.feature_names);check()
            if (type(values) is not np.ndarray or values.dtype!=np.float32 or values.shape!=(len(requests),)
                    or not np.isfinite(values).all() or np.any(np.abs(values)>100_000_000)):raise ValueError()
            for series,value in zip(binding.series_ids,values):
                scope=ForecastScope(plan_digest=d.candidate.training_plan_digest,dataset_version=binding.reference.version_id,
                    fold_id=d.coverage.fold_id,organization_id=binding.reference.organization_id,series_id=series,
                    metric=d.relationship.metric,unit=d.relationship.unit,currency=d.relationship.currency,
                    frequency=d.relationship.frequency,timezone=d.relationship.timezone,
                    dimensions=tuple(sorted(definitions[series].dimensions.items())),forecast_origin=origin,
                    horizon_step=1,target_period=origin,target_period_end=end)
                event=FittedH1Prediction(point=PredictionPoint(scope=scope,value=float(value)),
                    request_id=identity([record.use_id,history_id,self.schema.schema_id,origin.isoformat()]),
                    history_identity=history_id,representation_schema_id=self.schema.schema_id,
                    native_float32_hex=struct.pack('<f',float(value)).hex())
                output_bytes+=len(event.model_dump_json(warnings='error').encode())
                if output_bytes>2*1024*1024:raise ValueError()
                events.append(event)
            terminal.append((ctx,claims))
        result=FittedH1PredictionResult(eligibility_id=d.eligibility_id,evaluation_use_id=record.use_id,
            relationship_approval_id=self.approval_id,candidate_bundle_id=d.candidate.candidate_bundle_id,
            artifact_digest=d.candidate.artifact_digest,training_invocation_digest=d.candidate.training_invocation_digest,
            training_plan_digest=d.candidate.training_plan_digest,fitted_state_id=self.state.state_id,
            fitted_state_digest=d.candidate.fitted_state_digest,representation_schema_id=self.schema.schema_id,
            configuration_digest=engine.digest,runtime_digest=d.candidate.runtime_digest,evaluation_publication=binding,
            numeric_policy_id=d.capabilities.numeric_policy_id,events=tuple(events))
        if len(events)!=len(d.coverage.periods)*len(binding.series_ids):raise ValueError()
        encoded=result.model_dump_json(warnings='error')
        if len(encoded.encode())>2*1024*1024:raise ValueError()
        result=FittedH1PredictionResult.model_validate_json(encoded)
        if shared is not None:
            # Original model owner records the actual object BEFORE publication.
            if self.__joint_record is not None:raise ValueError()
            self.__joint_record=(shared,result,result.prediction_id,tuple(terminal),meta,record)
        self._fresh('return',check,record,metadata=meta)
        for ctx,claims in terminal:
            check()
            if self.history(ctx,claims) is not True:raise ValueError()
            scoped=EvaluationUseContext(self.encoded,self.approval_id,record.use_id,ctx.forecast_origin)
            if self.authorize(scoped,'scoped_return') is not True:raise ValueError()
        self._gate(check,record)
        with self.lock:
            self._active(record);record.status='COMPLETED'
            return result

    def _joint_seal(self,shared,result):
        held=self.__joint_record
        if (held is None or held[0] is not shared or held[1] is not result
                or result.prediction_id!=held[2] or self.record is not held[5]
                or self.record.status!='COMPLETED'):raise ValueError()
        return held[2]

    def _joint_verify(self,shared,result,*,cancelled=_continue,broad=True,origin=None):
        self._joint_seal(shared,result)
        _,_,_,terminal,meta,record=self.__joint_record
        check=self._checkpoint(record,cancelled,sealed=True)
        if broad:self._fresh('joint_verify',check,record,origin,meta,sealed=True)
        else:
            for ctx,claims in terminal:
                check()
                if self.history(ctx,claims) is not True:raise ValueError()
                scoped=EvaluationUseContext(self.encoded,self.approval_id,record.use_id,ctx.forecast_origin)
                if self.authorize(scoped,'joint_scoped_return') is not True:raise ValueError()
            self._gate(check,record,sealed=True)

    def _joint_stop(self,shared,status):
        if self.__joint_record is not None and self.__joint_record[0] is not shared:raise ValueError()
        if status not in ('REVOKED','CANCELLED','FAILED'):raise ValueError()
        with self.lock:
            if self.record is not None:self.record.status=status

    def _context(self,meta,origin,end):
        d=self.declaration
        return TemporalPreparationContext(meta,self.engine.configuration.model_dump_json(),self.engine.digest,
            d.relationship.evaluation.series_ids,d.candidate.training_plan.model_dump_json(),
            d.candidate.training_plan_digest,d.coverage.fold_id,origin,
            self.engine._time(d.candidate.training_plan.folds[0].categorical_cutoff),end)


class EvaluationUseOperator:
    __slots__=('__owner',)
    def __init__(self,owner):
        if type(owner) is not _Owner:raise ModelPredictionError('invalid trusted prediction composition')
        self.__owner=owner
    def issue(self,*args,**kwargs):return _guard(self.__owner.issue,*args,**kwargs)
    def revoke(self,*args,**kwargs):return _guard(self.__owner.revoke,*args,**kwargs)
    def cancel(self,*args,**kwargs):return _guard(self.__owner.cancel,*args,**kwargs)


class ValidationH1Predictor:
    __slots__=('__owner',)
    def __init__(self,owner):
        if type(owner) is not _Owner:raise ModelPredictionError('invalid trusted prediction composition')
        self.__owner=owner
    def predict(self,*args,**kwargs):return _guard(self.__owner.predict,*args,**kwargs)


def _bootstrap_owned(current,material,**composition):
    owner=_Owner(current,material,**composition)
    return EvaluationUseOperator(owner),ValidationH1Predictor(owner)
