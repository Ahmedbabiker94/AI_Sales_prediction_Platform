"""D3-A declarations only: neither metadata nor hashes confer evaluation authority.

No issuer, native loader, predictor or scoring integration exists here. D3-B must
verify the original live D2 owner, measured runtime, current authorization and
independent publication relationship evidence. These declarations cannot do so.
"""
from datetime import timezone
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, model_validator

from .backtesting import Digest, identity
from .dataset import _opaque
from .model_candidate import CandidateBundle, RuntimeIdentity as _CandidateRuntimeIdentity, TrainingInvocation, digest
from src.features.feature_schema_compatibility import FeatureSchemaCompatibility
from .temporal_preparation import _TemporalContract, TemporalDatasetBinding, TemporalTimestamp, TemporalPreparationPlan, _instants


class EvaluationDeclarationError(ValueError):
    """Fixed diagnostics at the declaration import boundary."""


def _reject_declaration(*, identity=False):
    message=('invalid non-authoritative evaluation identity' if identity
             else 'invalid non-authoritative evaluation declaration')
    error=EvaluationDeclarationError(message)
    try:
        raise error from None
    finally:
        # A caller may already be handling another input-bearing exception.
        # Python attaches that ambient context even after our own handler exits.
        error.__context__=None
        error.__cause__=None


def _declaration_identity(value):
    try:
        return identity(value.model_dump(mode='json',warnings='error'))
    except Exception:
        pass
    _reject_declaration(identity=True)


def _validate_public(operation, *args, **kwargs):
    # Raise after leaving the handler: ``from None`` only hides __context__, it
    # does not remove the original, potentially sensitive ValidationError.
    try:
        if kwargs.get('extra') not in (None, 'forbid'):
            raise ValueError()
        return operation(*args, **kwargs)
    except Exception:
        pass
    _reject_declaration()


class _DeclarationMeta(type(_TemporalContract)):
    def __call__(cls, *args, **kwargs):
        # Do not override BaseModel.__init__: a custom Pydantic init can change
        # compiled nested-validation and strict/context dispatch behavior.
        return _validate_public(super().__call__, *args, **kwargs)


class _Declaration(_TemporalContract, metaclass=_DeclarationMeta):
    model_config = ConfigDict(frozen=True, revalidate_instances='always', hide_input_in_errors=True)

    @classmethod
    def model_validate(cls, *args, **kwargs):
        return _validate_public(super().model_validate, *args, **kwargs)

    @classmethod
    def model_validate_json(cls, *args, **kwargs):
        return _validate_public(super().model_validate_json, *args, **kwargs)

    @classmethod
    def model_validate_strings(cls, *args, **kwargs):
        return _validate_public(super().model_validate_strings, *args, **kwargs)


class RuntimeIdentity(_Declaration, _CandidateRuntimeIdentity):
    # D3-local facade. Identical fields/wire name; D2's runtime API is unchanged.
    pass


class FittedCandidateCapabilities(_Declaration):
    """Separate branch; deliberately NOT accepted by synthetic B1/C1/C2 APIs."""
    version: Literal['1.1'] = '1.1'
    implementation_kind: Literal['fitted_candidate'] = 'fitted_candidate'
    model_family: Literal['xgboost'] = 'xgboost'
    strategy: Literal['recursive_one_step'] = 'recursive_one_step'
    representation_kind: Literal['tabular_step'] = 'tabular_step'
    supported_horizon: StrictInt = Field(default=1, ge=1, le=1)
    prediction_coverage: Literal['validation_only_h1'] = 'validation_only_h1'
    ownership_requirement: Literal['same_process_original_live_d2_issuer'] = 'same_process_original_live_d2_issuer'
    candidate_bundle_id: Digest
    artifact_digest: Digest
    training_handoff_id: Digest
    configuration_digest: Digest
    fitted_state_id: Digest
    fitted_state_digest: Digest
    representation_schema_id: Digest
    feature_names: tuple[str, ...] = Field(min_length=1, max_length=32)
    feature_dtypes: tuple[str, ...] = Field(min_length=1, max_length=32)
    allowed_covariates: tuple[str,...] = Field(default=(),max_length=128)
    model_input_policy: Literal['strict_exact_float32_v1'] = 'strict_exact_float32_v1'
    native_output_policy: Literal['finite_float32_v1'] = 'finite_float32_v1'
    widening_policy: Literal['exact_float32_to_float64_v1'] = 'exact_float32_to_float64_v1'

    @model_validator(mode='after')
    def ordered_representation(self):
        if len(self.feature_names)!=len(self.feature_dtypes) or len(set(self.feature_names))!=len(self.feature_names):
            raise ValueError('complete unique ordered representation required')
        for name in self.feature_names+self.allowed_covariates: _opaque(name)
        if tuple(sorted(set(self.allowed_covariates)))!=self.allowed_covariates:
            raise ValueError('ordered unique allowed covariates required')
        if any(t not in ('float64','int64','int32','int16','int8','bool') for t in self.feature_dtypes):
            raise ValueError('unsupported representation dtype')
        return self

    @property
    def numeric_policy_id(self):
        # Full original configuration binds canonical conversion mode, scopes,
        # tolerances and units. This is a D3 identity, not B1's policy identifier.
        return identity(['d3_numeric_v1',self.configuration_digest,self.model_input_policy,
                         self.native_output_policy,self.widening_policy])

    @property
    def digest(self): return _declaration_identity(self)


class EvaluationResourcePolicy(_Declaration):
    version: Literal['1.0'] = '1.0'
    max_captured_rows: StrictInt = Field(default=10000, ge=1, le=10000)
    max_decoded_bytes: StrictInt = Field(default=16*1024*1024, ge=1, le=16*1024*1024)
    max_representation_bytes: StrictInt = Field(default=2*1024*1024, ge=1, le=2*1024*1024)
    max_origins: StrictInt = Field(default=64, ge=1, le=64)
    max_prediction_rows: StrictInt = Field(default=512, ge=1, le=512)
    max_native_calls: StrictInt = Field(default=64, ge=1, le=64)
    max_input_cells: StrictInt = Field(default=8192, ge=1, le=8192)
    max_artifact_bytes: StrictInt = Field(default=1024*1024, ge=1, le=1024*1024)
    max_bundle_bytes: StrictInt = Field(default=2*1024*1024, ge=1, le=2*1024*1024)
    max_state_bytes: StrictInt = Field(default=4*1024*1024, ge=1, le=4*1024*1024)
    max_elapsed_seconds: StrictInt = Field(default=60, ge=1, le=60)
    max_verification_calls: StrictInt = Field(default=512, ge=1, le=512)
    # Declarative ceilings, NOT runtime counters, clocks or hard process limits.


class SeriesEvaluationScope(_Declaration):
    series_id: UUID
    dimensions: tuple[tuple[str,str], ...] = Field(max_length=128)

    @model_validator(mode='after')
    def unique_dimensions(self):
        if tuple(sorted(self.dimensions))!=self.dimensions or len(dict(self.dimensions))!=len(self.dimensions):
            raise ValueError('ordered unique dimension scope required')
        for key,value in self.dimensions: _opaque(key); _opaque(value)
        return self


class PublicationRelationshipDeclaration(_Declaration):
    """Operator evidence requirements, NOT independently verified metadata."""
    version: Literal['1.0'] = '1.0'
    relationship_id: str = Field(min_length=1, max_length=256)
    authority_id: str = Field(min_length=1, max_length=256)
    authority_version: str = Field(min_length=1, max_length=64)
    evidence_references: tuple[str,...] = Field(min_length=1, max_length=32)
    required_protocol: Literal['training_evaluation_relationship_v1'] = 'training_evaluation_relationship_v1'
    training: TemporalDatasetBinding
    evaluation: TemporalDatasetBinding
    training_purpose: Literal['production_training'] = 'production_training'
    evaluation_purpose: Literal['offline_forecast_evaluation'] = 'offline_forecast_evaluation'
    metric: str
    unit: str
    currency: str | None
    frequency: Literal['daily','weekly']
    calendar: Literal['gregorian']
    timezone: str
    period_anchor: Literal['start'] = 'start'
    series: tuple[SeriesEvaluationScope,...] = Field(min_length=1,max_length=128)

    @model_validator(mode='after')
    def related_resources(self):
        for text in (self.relationship_id,self.authority_id,self.authority_version,self.metric,self.unit,self.timezone)+self.evidence_references:
            _opaque(text)
        if self.currency is not None: _opaque(self.currency)
        if len(set(self.evidence_references))!=len(self.evidence_references):
            raise ValueError('unique relationship evidence references required')
        left,right=self.training,self.evaluation
        if (left.reference.organization_id!=right.reference.organization_id
                or left.reference.version_id==right.reference.version_id):
            raise ValueError('distinct same-organization purpose publications required')
        fields=('source_id','mapping_id','mapping_version','mapping_digest','series_definition_digest',
                'series_ids','quality_policy_id','quality_policy_version')
        if any(getattr(left,k)!=getattr(right,k) for k in fields):
            raise ValueError('incompatible publication relationship')
        if tuple(s.series_id for s in self.series)!=left.series_ids:
            raise ValueError('exact ordered series scope required')
        return self


class ValidationH1Period(_Declaration):
    forecast_origin: TemporalTimestamp
    horizon_step: StrictInt = Field(default=1,ge=1,le=1)
    target_period: TemporalTimestamp
    target_period_end: TemporalTimestamp


class ValidationOnlyCoverage(_Declaration):
    version: Literal['1.0'] = '1.0'
    execution: Literal['validation_only_h1'] = 'validation_only_h1'
    fold_id: str
    periods: tuple[ValidationH1Period,...] = Field(min_length=1,max_length=64)
    refitting: Literal['forbidden'] = 'forbidden'

    @model_validator(mode='after')
    def ordered_origins(self):
        _opaque(self.fold_id)
        dates=_instants(tuple(p.forecast_origin for p in self.periods))
        if tuple(sorted(set(dates)))!=dates:
            raise ValueError('ordered unique validation origins required')
        return self


class EvaluationAuthorizationRequirements(_Declaration):
    """Future issuer obligations. No callback or approval token can be supplied."""
    version: Literal['1.0'] = '1.0'
    issuance: Literal['NOT_IMPLEMENTED'] = 'NOT_IMPLEMENTED'
    purpose: Literal['offline_forecast_evaluation'] = 'offline_forecast_evaluation'
    issuer: Literal['original_live_d2_issuer'] = 'original_live_d2_issuer'
    training_handoff: Literal['original_issued_d1_handoff'] = 'original_issued_d1_handoff'
    candidate_reconstruction: Literal['forbidden'] = 'forbidden'
    revocation: Literal['fresh_scope_specific_checks_required'] = 'fresh_scope_specific_checks_required'
    checkpoints: Literal['after_final_callbacks_and_before_return'] = 'after_final_callbacks_and_before_return'
    cancellation: Literal['cooperative_required'] = 'cooperative_required'
    deadline: Literal['elapsed_ge_budget_reject'] = 'elapsed_ge_budget_reject'
    terminal_ordering: Literal['broader_checks_then_terminal_scopes'] = 'broader_checks_then_terminal_scopes'
    required_stages: tuple[Literal['admit','before_load','origin','before_handoff'], ...] = (
        'admit','before_load','origin','before_handoff')

    @model_validator(mode='after')
    def full_lifecycle(self):
        if self.required_stages!=('admit','before_load','origin','before_handoff'):
            raise ValueError('complete evaluation authorization lifecycle required')
        return self


class LabelFreeCandidateProvenance(_Declaration):
    """An allowlisted summary. No model bytes, training rows, labels or state data.

    Digests bind the owner's original private records; they grant no authority.
    The original full plan and feature schema contain declarations, not examples.
    """
    version: Literal['1.0'] = '1.0'
    candidate_bundle_id: Digest
    manifest_digest: Digest
    artifact_digest: Digest
    artifact_size: StrictInt = Field(ge=1,le=1024*1024)
    bundle_size: StrictInt = Field(ge=1,le=2*1024*1024)
    model_family: Literal['xgboost'] = 'xgboost'
    serialization: Literal['ubj'] = 'ubj'
    model_input_policy: Literal['strict_exact_float32_v1'] = 'strict_exact_float32_v1'
    training_invocation: TrainingInvocation
    training_invocation_digest: Digest
    training_scope_id: str
    training_scope_digest: Digest
    label_authority_id: str
    label_authority_version: str
    training_handoff_id: Digest
    preparation_id: Digest
    training_plan: TemporalPreparationPlan
    training_plan_digest: Digest
    configuration_digest: Digest
    fitted_state_id: Digest
    fitted_state_digest: Digest
    fitted_state_size: StrictInt = Field(ge=1,le=4*1024*1024)
    training_partition_reference: Digest
    training_schema_digest: Digest
    representation: FeatureSchemaCompatibility
    series: tuple[SeriesEvaluationScope,...] = Field(min_length=1,max_length=128)
    runtime_digest: Digest

    @model_validator(mode='wrap')
    @classmethod
    def sanitized_projection(cls,value,handler):
        # hide_input_in_errors does not hide caller-controlled extra-field names
        # in nested Pydantic locations. Keep this new summary boundary fixed-text.
        return _validate_public(handler, value)

    @model_validator(mode='after')
    def original_bindings(self):
        for name in (self.training_scope_id,self.label_authority_id,self.label_authority_version):_opaque(name)
        plan=self.training_plan;schema=self.representation
        if (len(plan.folds)!=1 or len(plan.datasets)!=1 or plan.forecast_horizon!=1
                or plan.capability!='supervised_h1' or self.training_plan_digest!=plan.digest
                or self.configuration_digest!=plan.configuration_digest
                or self.configuration_digest!=schema.configuration_digest
                or schema.forecast_horizon!=1 or schema.fitted_state_id!=self.fitted_state_id
                or schema.training_schema_digest!=self.training_schema_digest
                or tuple(s.series_id for s in self.series)!=plan.datasets[0].series_ids
                or self.training_invocation_digest!=digest(self.training_invocation.model_dump_json(warnings='error').encode())):
            raise ValueError('inconsistent label-free candidate provenance')
        # Preserve D1's exact calibration-partition identity without exporting
        # its label claims or fitted vocabulary. Calendar/hash helpers are shared.
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        engine=CanonicalFeaturePipeline(schema.configuration);fold=plan.folds[0]
        partition=identity([plan.digest,fold.fold_id,plan.datasets[0].model_dump(mode='json'),
            self.configuration_digest,engine._time(fold.calibration_start).isoformat(),
            engine._time(fold.categorical_cutoff).isoformat()])
        if self.training_partition_reference!=partition:
            raise ValueError('original fitted calibration partition required')
        return self


class ModelEvaluationEligibility(_Declaration):
    """Label-free consistency declaration, NEVER an issued evaluation handle.

    Original manifests and label-bearing evidence stay with the original owner.
    Ownership, purpose admission, relationship authority, measured runtime and
    revocation remain D3-B/C duties. No digest or public summary proves them.
    """
    # Rejected manifest-bearing wire form is deliberately not deserializable.
    version: Literal['1.1'] = '1.1'
    status: Literal['DECLARATION_ONLY'] = 'DECLARATION_ONLY'
    candidate: LabelFreeCandidateProvenance
    capabilities: FittedCandidateCapabilities
    relationship: PublicationRelationshipDeclaration
    coverage: ValidationOnlyCoverage
    runtime: RuntimeIdentity
    resources: EvaluationResourcePolicy = Field(default_factory=EvaluationResourcePolicy)
    authorization: EvaluationAuthorizationRequirements = Field(default_factory=EvaluationAuthorizationRequirements)

    @model_validator(mode='after')
    def consistent_declaration(self):
        from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
        candidate=self.candidate;cap=self.capabilities;relation=self.relationship
        schema=candidate.representation;engine=CanonicalFeaturePipeline(schema.configuration)
        expected=(candidate.candidate_bundle_id,candidate.artifact_digest,candidate.training_handoff_id,
                  candidate.configuration_digest,candidate.fitted_state_id,candidate.fitted_state_digest,schema.schema_id,
                  schema.configuration.ordered_outputs,tuple(schema.configuration.output_dtypes[n] for n in schema.configuration.ordered_outputs))
        actual=(cap.candidate_bundle_id,cap.artifact_digest,cap.training_handoff_id,cap.configuration_digest,
                cap.fitted_state_id,cap.fitted_state_digest,cap.representation_schema_id,cap.feature_names,cap.feature_dtypes)
        if (actual!=expected or digest(self.runtime.model_dump_json(warnings='error').encode())!=candidate.runtime_digest
                or not self.runtime.source_matches_commit):
            raise ValueError('original candidate state representation or runtime mismatch')
        plan=candidate.training_plan;fold=plan.folds[0]
        if relation.training!=plan.datasets[0] or self.coverage.fold_id!=fold.fold_id:
            raise ValueError('original single-fold training provenance required')
        rules=engine.rules;temporal=engine.temporal
        if cap.allowed_covariates!=tuple(sorted(r.field for r in rules.exogenous)):
            raise ValueError('original covariate scope required')
        if ((relation.metric,relation.unit,relation.currency)!=(rules.measurement.metric,rules.measurement.unit,rules.measurement.currency)
                or (relation.frequency,relation.calendar,relation.timezone,relation.period_anchor)!=(
                    temporal.frequency.value,temporal.calendar,temporal.timezone,temporal.period_anchor)
                or (plan.frequency,plan.calendar,plan.timezone,plan.period_anchor)!=(
                    relation.frequency,relation.calendar,relation.timezone,relation.period_anchor)):
            raise ValueError('measurement or calendar relationship mismatch')
        if relation.series!=candidate.series:
            raise ValueError('original series dimensions required')
        begin=engine._time(fold.calibration_start);cutoff=engine._time(fold.categorical_cutoff)
        fit=engine._time(fold.model_fit_cutoff);assessment=engine._time(fold.assessment_cutoff)
        for date in (begin,cutoff)+tuple(engine._time(o) for o in fold.origins):engine._aligned(date)
        # Calendar arithmetic belongs exclusively to the accepted causal executor.
        training=tuple((engine._time(o),engine._advance(engine._time(o),1)) for o in fold.training_origins)
        test=tuple((engine._time(o),engine._advance(engine._time(o),1)) for o in fold.test_origins)
        if any(end>fit for _,end in training):raise ValueError('training target extends beyond M')
        validation=set(_instants(fold.validation_origins))
        for period in self.coverage.periods:
            origin=engine._time(period.forecast_origin);start=engine._time(period.target_period);end=engine._time(period.target_period_end)
            engine._aligned(origin)
            if (period.forecast_origin.astimezone(timezone.utc) not in validation
                    or origin!=start or end!=engine._advance(origin,1) or not cutoff<=fit<=origin or end>assessment
                    or any(start<right and left<end for left,right in training+test)):
                raise ValueError('ineligible validation H1 target interval')
        b=self.resources;count=len(self.coverage.periods);rows=count*len(relation.series)
        if (count>min(b.max_origins,b.max_native_calls) or rows>b.max_prediction_rows
                or rows*len(cap.feature_names)>b.max_input_cells or candidate.artifact_size>b.max_artifact_bytes
                or candidate.fitted_state_size>b.max_state_bytes
                or candidate.bundle_size>b.max_bundle_bytes):
            raise ValueError('evaluation declaration exceeds resource ceilings')
        return self

    @property
    def eligibility_id(self): return _declaration_identity(self)


def _label_free_provenance(bundle,manifest):
    """Transient full-content validation precedes this allowlisted projection.

    Never retain the manifest, its JSON, label claims or buffers in the result.
    """
    provenance=manifest.provenance;native=provenance.states[0];state=native.state
    dimensions={}
    for claim in provenance.labels:
        if claim.series_id in dimensions and dimensions[claim.series_id]!=claim.dimensions:
            raise ValueError('conflicting original series dimensions')
        dimensions[claim.series_id]=claim.dimensions
    return LabelFreeCandidateProvenance(candidate_bundle_id=bundle.bundle_id,
        manifest_digest=digest(manifest.model_dump_json(warnings='error').encode()),
        artifact_digest=manifest.artifact_digest,artifact_size=manifest.artifact_size,bundle_size=len(bundle.payload),
        model_family=manifest.training.model_family,serialization=manifest.training.serialization,
        model_input_policy=manifest.training.input_policy,training_invocation=manifest.invocation,
        training_invocation_digest=digest(manifest.invocation.model_dump_json(warnings='error').encode()),
        training_scope_id=manifest.scope.scope_id,training_scope_digest=manifest.scope.digest,
        label_authority_id=manifest.scope.label_authority_id,label_authority_version=manifest.scope.label_authority_version,
        training_handoff_id=provenance.handoff_id,preparation_id=provenance.preparation_id,
        training_plan=provenance.plan,training_plan_digest=provenance.plan.digest,
        configuration_digest=provenance.configuration_digest,fitted_state_id=state.state_id,
        fitted_state_digest=manifest.state_digest,fitted_state_size=len(native.state_json.encode()),
        training_partition_reference=state.training_partition_reference,training_schema_digest=state.training_schema_digest,
        representation=FeatureSchemaCompatibility.model_validate_json(native.representation_json),
        series=tuple(SeriesEvaluationScope(series_id=sid,dimensions=dimensions[sid])
                     for sid in provenance.plan.datasets[0].series_ids),
        runtime_digest=digest(manifest.runtime.model_dump_json(warnings='error').encode()))


def declare_evaluation_eligibility(bundle, *, capabilities, relationship, coverage, runtime, resources=None):
    """Sanitized CONTENT validation only. Publicly reconstructable, NOT issuance.

    Full original content is inspected locally and never exported or cached.
    D3-B must verify the original live owner; this function grants no operation.
    """
    try:
        def snapshot(value,contract):
            if type(value) is not contract:raise ValueError()
            validated=contract.model_validate(value)
            return contract.model_validate(validated.model_dump(warnings='error'))
        cap=snapshot(capabilities,FittedCandidateCapabilities)
        relation=snapshot(relationship,PublicationRelationshipDeclaration)
        selected=snapshot(coverage,ValidationOnlyCoverage)
        if type(runtime) not in (_CandidateRuntimeIdentity,RuntimeIdentity):raise ValueError()
        checked_runtime=type(runtime).model_validate(runtime)
        observed=RuntimeIdentity.model_validate(checked_runtime.model_dump(warnings='error'))
        limits=snapshot(resources,EvaluationResourcePolicy) if resources is not None else EvaluationResourcePolicy()
        if type(bundle) is not CandidateBundle or type(bundle.payload) is not bytes or len(bundle.payload)>limits.max_bundle_bytes:
            raise ValueError()
        manifest,_=bundle.validate()
        if cap.candidate_bundle_id!=bundle.bundle_id:raise ValueError()
        return ModelEvaluationEligibility(candidate=_label_free_provenance(bundle,manifest),
            capabilities=cap,relationship=relation,coverage=selected,runtime=observed,resources=limits)
    except Exception:
        pass
    _reject_declaration()
