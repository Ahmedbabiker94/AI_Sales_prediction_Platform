"""C0 label-free diagnostics. Neither declarations nor seal hashes grant authority."""
from typing import Literal

import pandas as pd
from pydantic import Field, StrictInt, model_validator

from .backtesting import Digest, ForecastScope, PredictionPoint, PredictorCapabilities, identity
from .forecast_evaluation import BaselineDefinition
from .model_evaluation import _Declaration, _declaration_identity, ModelEvaluationEligibility
from .model_prediction import FittedH1PredictionResult


class JointH1Budgets(_Declaration):
    max_snapshot_bytes: StrictInt = Field(default=32*1024*1024,ge=1,le=32*1024*1024)
    max_baseline_work: StrictInt = Field(default=1000000,ge=1,le=1000000)
    max_output_bytes: StrictInt = Field(default=2*1024*1024,ge=1,le=2*1024*1024)
    max_verifications: StrictInt = Field(default=64,ge=1,le=64)


class JointValidationH1Coverage(_Declaration):
    version: Literal['1.0'] = '1.0'
    mode: Literal['validation_only_h1'] = 'validation_only_h1'
    scopes: tuple[ForecastScope,...] = Field(min_length=1,max_length=512)

    @model_validator(mode='after')
    def geometry(self):
        keys=[]
        for s in self.scopes:
            o=pd.Timestamp(s.forecast_origin).tz_convert(s.timezone)
            if (s.horizon_step!=1 or s.target_period!=s.forecast_origin
                    or pd.Timestamp(s.target_period_end)!=o+pd.DateOffset(days=1 if s.frequency=='daily' else 7)):
                raise ValueError('validation H1 geometry required')
            keys.append((s.forecast_origin,str(s.organization_id),str(s.series_id)))
        if keys!=sorted(set(keys)):raise ValueError('ordered unique coverage required')
        return self

    @property
    def coverage_id(self):return _declaration_identity(self)


class SealedJointH1Result(_Declaration):
    version: Literal['1.0'] = '1.0'
    status: Literal['SEALED_NOT_SCORED'] = 'SEALED_NOT_SCORED'
    eligibility: ModelEvaluationEligibility
    joint_use_id: Digest
    capture_id: Digest
    seal_id: Digest
    model_handoff_id: Digest
    baseline_handoff_id: Digest
    baseline_owner_id: Digest
    baseline_configuration_digest: Digest
    baseline_numeric_policy_id: Digest
    baseline_definition: BaselineDefinition
    baseline_capabilities: PredictorCapabilities
    coverage: JointValidationH1Coverage
    model: FittedH1PredictionResult
    baseline_points: tuple[PredictionPoint,...] = Field(min_length=1,max_length=512)
    history_ids: tuple[Digest,...] = Field(min_length=1,max_length=512)

    @model_validator(mode='after')
    def grid(self):
        d=self.eligibility;m=self.model
        if (m.eligibility_id!=d.eligibility_id or m.candidate_bundle_id!=d.candidate.candidate_bundle_id
                or m.artifact_digest!=d.candidate.artifact_digest
                or m.training_invocation_digest!=d.candidate.training_invocation_digest
                or m.training_plan_digest!=d.candidate.training_plan_digest
                or m.fitted_state_id!=d.candidate.fitted_state_id or m.fitted_state_digest!=d.candidate.fitted_state_digest
                or m.representation_schema_id!=d.capabilities.representation_schema_id
                or m.configuration_digest!=d.candidate.configuration_digest
                or m.runtime_digest!=d.candidate.runtime_digest or m.numeric_policy_id!=d.capabilities.numeric_policy_id
                or m.evaluation_publication!=d.relationship.evaluation):raise ValueError('original bindings required')
        scopes=tuple(e.point.scope for e in m.events)
        if (scopes!=self.coverage.scopes or tuple(p.scope for p in self.baseline_points)!=scopes
                or tuple(e.history_identity for e in m.events)!=self.history_ids):raise ValueError('identical ordered grid required')
        expected=tuple((p,s) for p in d.coverage.periods for s in d.relationship.evaluation.series_ids)
        if len(scopes)!=len(expected):raise ValueError('complete validation grid required')
        dimensions={s.series_id:s.dimensions for s in d.relationship.series}
        relation=d.relationship
        for scope,(period,series) in zip(scopes,expected):
            if ((scope.metric,scope.unit,scope.currency,scope.frequency,scope.timezone)!=(relation.metric,relation.unit,
                    relation.currency,relation.frequency,relation.timezone) or scope.dimensions!=dimensions[series]
                    or scope.forecast_origin!=period.forecast_origin or scope.target_period!=period.target_period
                    or scope.target_period_end!=period.target_period_end or scope.series_id!=series
                    or scope.fold_id!=d.coverage.fold_id):raise ValueError('approved validation coverage required')
        definition=self.baseline_definition;cap=self.baseline_capabilities
        if (definition.supported_horizon!=1 or cap.predictor_id!='baseline-'+definition.digest
                or cap.supported_horizon!=1 or cap.lookback!=definition.lookback
                or cap.implementation_kind!='synthetic' or cap.numeric_policy_id!=self.baseline_numeric_policy_id):
            raise ValueError('fixed H1 baseline required')
        from src.features.forecast_baselines import build_baseline_profile
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        profile=build_baseline_profile(definition,d.candidate.representation.configuration)
        if (profile.capabilities!=cap or CanonicalFeaturePipeline(profile.configuration).digest!=self.baseline_configuration_digest
                or m.prediction_id!=self.model_handoff_id
                or identity([p.model_dump(mode='json',warnings='error') for p in self.baseline_points])!=self.baseline_handoff_id
                or self.seal_id!=identity(['joint_h1_seal_v1',self.joint_use_id,self.capture_id,self.model_handoff_id,
                    self.baseline_handoff_id,self.baseline_owner_id])):raise ValueError('exact handoff bindings required')
        return self

    @property
    def result_id(self):return _declaration_identity(self)
