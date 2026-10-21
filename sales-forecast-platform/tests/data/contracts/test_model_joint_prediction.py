"""C0 declarations are detached diagnostics, never owner-issued credentials."""
import runpy
import struct
import traceback
import warnings
from uuid import uuid4
import pytest
from src.data.contracts.backtesting import ForecastScope,PredictionPoint,identity
from src.data.contracts.forecast_evaluation import BaselineDefinition
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_prediction import FittedH1Prediction,FittedH1PredictionResult
from src.data.contracts.model_joint_prediction import JointH1Budgets,JointValidationH1Coverage,SealedJointH1Result
from src.features.canonical_feature_pipeline import CanonicalFeaturePipeline
from src.features.forecast_baselines import build_baseline_profile

@pytest.fixture(scope='module')
def declaration(tmp_path_factory):
    ns=runpy.run_path('tests/data/contracts/test_model_evaluation.py')
    return ns['declare'](ns['metadata'].__wrapped__(tmp_path_factory))

@pytest.fixture
def result(declaration):
    d=declaration;r=d.relationship;events=[]
    for period in d.coverage.periods:
        for series in r.series:
            scope=ForecastScope(plan_digest=d.candidate.training_plan_digest,dataset_version=r.evaluation.reference.version_id,
                fold_id=d.coverage.fold_id,organization_id=r.evaluation.reference.organization_id,series_id=series.series_id,
                metric=r.metric,unit=r.unit,currency=r.currency,frequency=r.frequency,timezone=r.timezone,dimensions=series.dimensions,
                forecast_origin=period.forecast_origin,horizon_step=1,target_period=period.target_period,target_period_end=period.target_period_end)
            events.append(FittedH1Prediction(point=PredictionPoint(scope=scope,value=1.5),request_id='1'*64,
                history_identity='2'*64,representation_schema_id=d.capabilities.representation_schema_id,native_float32_hex=struct.pack('<f',1.5).hex()))
    model=FittedH1PredictionResult(eligibility_id=d.eligibility_id,evaluation_use_id='3'*64,relationship_approval_id='4'*64,
        candidate_bundle_id=d.candidate.candidate_bundle_id,artifact_digest=d.candidate.artifact_digest,
        training_invocation_digest=d.candidate.training_invocation_digest,training_plan_digest=d.candidate.training_plan_digest,
        fitted_state_id=d.candidate.fitted_state_id,fitted_state_digest=d.candidate.fitted_state_digest,
        representation_schema_id=d.capabilities.representation_schema_id,configuration_digest=d.candidate.configuration_digest,
        runtime_digest=d.candidate.runtime_digest,evaluation_publication=r.evaluation,numeric_policy_id=d.capabilities.numeric_policy_id,events=tuple(events))
    definition=BaselineDefinition(algorithm='last_observation_v1',lookback=1,supported_horizon=1)
    profile=build_baseline_profile(definition,d.candidate.representation.configuration)
    points=tuple(PredictionPoint(scope=e.point.scope,value=0.) for e in events)
    baseline_id=identity([p.model_dump(mode='json') for p in points]);model_id=model.prediction_id
    return SealedJointH1Result(eligibility=d,joint_use_id='5'*64,capture_id='6'*64,
        seal_id=identity(['joint_h1_seal_v1','5'*64,'6'*64,model_id,baseline_id,'7'*64]),
        model_handoff_id=model_id,baseline_handoff_id=baseline_id,baseline_owner_id='7'*64,
        baseline_configuration_digest=CanonicalFeaturePipeline(profile.configuration).digest,
        baseline_numeric_policy_id=profile.capabilities.numeric_policy_id,baseline_definition=definition,
        baseline_capabilities=profile.capabilities,coverage=JointValidationH1Coverage(scopes=tuple(e.point.scope for e in events)),
        model=model,baseline_points=points,history_ids=tuple(e.history_identity for e in events))


def test_label_free_roundtrip_and_immutability(result):
    encoded=result.model_dump_json();assert SealedJointH1Result.model_validate_json(encoded).model_dump_json()==encoded
    assert result.result_id==SealedJointH1Result.model_validate_json(encoded).result_id
    with pytest.raises(ValueError):result.capture_id='9'*64
    pending=[result.model_dump()];forbidden={'labels','manifest','state_json','provenance_json','artifact_base64','matrix_float32_hex','predictions_float32_hex'}
    while pending:
        item=pending.pop()
        if isinstance(item,dict):assert not forbidden.intersection(item);pending.extend(item.values())
        elif isinstance(item,(list,tuple)):pending.extend(item)

@pytest.mark.parametrize('field',['capture_id','model_handoff_id','baseline_handoff_id','baseline_owner_id','seal_id','baseline_configuration_digest','baseline_numeric_policy_id'])
def test_substituted_bindings_reject(result,field):
    data=result.model_dump();data[field]='a'*64
    with pytest.raises(EvaluationDeclarationError):SealedJointH1Result.model_validate(data)

@pytest.mark.parametrize('kind',['missing','duplicate','extra','wrong_origin','wrong_target','h2','h3','wrong_series'])
def test_invalid_grid_reject(result,kind):
    data=result.model_dump();p=data['baseline_points'][0]
    if kind=='missing':data['baseline_points']=()
    elif kind in ('duplicate','extra'):data['baseline_points']=data['baseline_points']+(p,)
    elif kind=='wrong_series':p['scope']['series_id']=uuid4()
    elif kind=='wrong_origin':p['scope']['forecast_origin']=result.eligibility.candidate.training_plan.folds[0].training_origins[0]
    elif kind=='wrong_target':p['scope']['target_period']=p['scope']['target_period_end']
    else:p['scope']['horizon_step']=2 if kind=='h2' else 3
    with pytest.raises(EvaluationDeclarationError):SealedJointH1Result.model_validate(data)

@pytest.mark.parametrize('entry',['constructor','python','json','strings','identity'])
def test_secret_diagnostics(entry,capsys,caplog):
    secret='C0_SECRET_'+uuid4().hex;data={secret:secret}
    operations=dict(constructor=lambda:SealedJointH1Result(**data),python=lambda:SealedJointH1Result.model_validate(data),
        json=lambda:SealedJointH1Result.model_validate_json(__import__('json').dumps(data)),
        strings=lambda:SealedJointH1Result.model_validate_strings(data),identity=lambda:SealedJointH1Result.model_construct(model=data).result_id)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as e:operations[entry]()
    out=capsys.readouterr();error=e.value
    assert secret not in str(error)+repr(error)+repr(error.args)+''.join(traceback.format_exception(error))+out.err+out.out+caplog.text+repr(seen)
    assert error.__context__ is None and error.__cause__ is None

@pytest.mark.parametrize('field',list(JointH1Budgets.model_fields))
def test_resource_booleans_reject(field):
    with pytest.raises(ValueError):JointH1Budgets(**{field:True})
