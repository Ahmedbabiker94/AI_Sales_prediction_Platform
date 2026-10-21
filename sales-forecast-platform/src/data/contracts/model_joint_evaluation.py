"""C1 exact score diagnostics. Construction grants no session or label authority."""
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from .backtesting import Digest
from .model_evaluation import _Declaration, _declaration_identity
from .model_joint_prediction import SealedJointH1Result
from .forecast_evaluation import ForecastEvaluationResult
from .forecast_comparison import JointScoreDelta


class JointH1ScoringBudgets(_Declaration):
    max_label_rows: StrictInt = Field(default=4096,ge=1,le=20000)
    max_label_bytes: StrictInt = Field(default=16*1024*1024,ge=1,le=64*1024*1024)
    max_points: StrictInt = Field(default=512,ge=1,le=512)
    max_groups: StrictInt = Field(default=512,ge=1,le=512)
    max_representation_bytes: StrictInt = Field(default=64*1024*1024,ge=1,le=512*1024*1024)
    max_work: StrictInt = Field(default=4000000,ge=1,le=4000000)
    max_arithmetic_units: StrictInt = Field(default=2**46,ge=1,le=2**46)
    max_output_bytes: StrictInt = Field(default=8*1024*1024,ge=1,le=32*1024*1024)
    max_elapsed_seconds: StrictInt = Field(default=60,ge=1,le=60)


class JointH1EvaluationResult(_Declaration):
    version: Literal['1.0'] = '1.0'
    status: Literal['TERMINAL_SCORED_NOT_PRODUCTION_APPROVED'] = 'TERMINAL_SCORED_NOT_PRODUCTION_APPROVED'
    sealed: SealedJointH1Result
    label_use_id: Digest
    label_authority_id: str = Field(min_length=1,max_length=256)
    label_authority_version: str = Field(min_length=1,max_length=64)
    label_authority_digest: Digest
    label_revision_ids: tuple[Digest,...] = Field(min_length=1,max_length=512)
    model_scores: ForecastEvaluationResult
    baseline_scores: ForecastEvaluationResult
    deltas: tuple[JointScoreDelta,...] = Field(min_length=1,max_length=512)

    @model_validator(mode='after')
    def bindings(self):
        a,b=self.model_scores,self.baseline_scores
        scopes=self.sealed.coverage.scopes
        if (tuple(e.scope for e in a.events)!=scopes or tuple(e.scope for e in b.events)!=scopes
                or tuple(e.label_binding_digest for e in a.events)!=self.label_revision_ids
                or tuple(e.label_binding_digest for e in b.events)!=self.label_revision_ids
                or a.producer_result_id!=self.sealed.model_handoff_id
                or b.producer_result_id!=self.sealed.baseline_handoff_id
                or len(self.deltas)!=len(a.groups) or len(a.groups)!=len(b.groups)):
            raise ValueError('original shared truth and handoffs required')
        expected_cutoff=self.sealed.eligibility.candidate.training_plan.folds[0].assessment_cutoff
        for evaluation,handoff in ((a,self.sealed.model_handoff_id),(b,self.sealed.baseline_handoff_id)):
            plan=evaluation.scoring_plan
            if (plan.producer_plan_digest!=handoff or plan.partitions!=('validation',)
                    or plan.datasets!=(self.sealed.eligibility.relationship.evaluation,)
                    or any(e.partition!='validation' or e.assessment_cutoff!=expected_cutoff for e in evaluation.events)):
                raise ValueError('exact validation publication and cutoff required')
        if a.scoring_plan.arithmetic!=b.scoring_plan.arithmetic:
            raise ValueError('identical exact scoring policy required')
        for delta,ga,gb in zip(self.deltas,a.groups,b.groups):
            if delta.candidate!=ga.statistics or delta.baseline!=gb.statistics:
                raise ValueError('exact corresponding score groups required')
        return self

    @property
    def evaluation_id(self):return _declaration_identity(self)
