"""Closed immutable model-independent declarations, never trust credentials."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import numpy as np
import pytest

from src.data.contracts.backtesting import (PredictorCapabilities, ForecastScope, PredictionPoint,
    PredictionBatch, PredictionEvent, RolloutCompatibilityBinding, PredictionRequest, point)
from src.data.contracts.dataset import StoredDatasetReference, DatasetAccessContext
from src.data.contracts.temporal_preparation import TemporalDatasetBinding

D='1'*64
O=datetime(2020,2,3,tzinfo=timezone.utc)


def cap(**changes):
    data=dict(predictor_id='synthetic',predictor_version='1',strategy='recursive_one_step',
        representation_kind='tabular_step',representation_schema_id=D,numeric_policy_id=D,lookback=4,supported_horizon=3)
    data.update(changes);return PredictorCapabilities(**data)


def scope(**changes):
    data=dict(plan_digest=D,dataset_version=D,fold_id='fold-1',organization_id=UUID(int=2),series_id=UUID(int=3),
        metric='sales',unit='USD',currency='USD',frequency='weekly',timezone='UTC',dimensions=(('location','north'),),
        forecast_origin=O,horizon_step=1,target_period=O,target_period_end=O+timedelta(days=7))
    data.update(changes);return ForecastScope(**data)


def binding():
    return TemporalDatasetBinding(reference=StoredDatasetReference(UUID(int=2),UUID(int=4),UUID(int=5),D),source_id=UUID(int=6),
        manifest_digest=D,admission_digest=D,mapping_id='mapping',mapping_version='1.0.0',mapping_digest=D,
        quality_policy_id='policy',quality_policy_version='1.0.0',series_definition_digest=D,series_ids=(UUID(int=3),))


def compatibility(**changes):
    data=dict(plan_digest=D,fold_id='fold-1',organization_id=UUID(int=2),forecast_origin=O,total_horizon=3,
        predictor_digest=cap().digest,representation_schema_id=D,configuration_digest=D,state_id=D,numeric_policy_id=D,
        prefix_schema_ids=(D,D,D))
    data.update(changes);return RolloutCompatibilityBinding(**data)


@pytest.mark.parametrize('value',[True,False,1,'1',None,float('nan'),float('inf'),np.float32(1),[1]])
def test_points_require_finite_exact_float64(value):
    with pytest.raises(ValueError):point(value)


def test_immutable_detached_request_and_float_bit_id():
    request=PredictionRequest(dataset_binding=binding(),capabilities=cap(),compatibility=compatibility(),prefix_length=1,
        prefix_schema_id=D,scopes=(scope(),),feature_names=('lag1',),feature_dtypes=('float64',),rows=((-0.,),))
    with pytest.raises(Exception):request.rows=((1.,),)
    matrix=request.matrix;matrix.iloc[0,0]=99.
    assert np.signbit(request.matrix.iloc[0,0])
    other=request.model_copy(update={'rows':((0.,),)})
    assert request.request_id!=other.request_id


@pytest.mark.parametrize('change',[dict(deterministic=1),dict(lookback=0),dict(supported_horizon=True),
    dict(supported_horizon=53),dict(version='2.0'),dict(allowed_covariates=('z','a')),dict(max_absolute_prediction=float('inf'))])
def test_capability_validation(change):
    with pytest.raises(ValueError):cap(**change)


def test_horizon_and_prefix_binding_are_distinct():
    one=compatibility(total_horizon=1,prefix_schema_ids=(D,))
    assert one.compatibility_id!=compatibility().compatibility_id
    with pytest.raises(ValueError):compatibility(total_horizon=2)


@pytest.mark.parametrize('strategy,kind',[('direct_multi_horizon','tabular_step'),('recursive_one_step','temporal_sequence'),
    ('recursive_one_step','statistical')])
def test_unsupported_declarations_do_not_create_executable_requests(strategy,kind):
    declaration=cap(strategy=strategy,representation_kind=kind)
    with pytest.raises(ValueError):PredictionRequest(dataset_binding=binding(),capabilities=declaration,
        compatibility=compatibility(predictor_digest=declaration.digest),prefix_length=1,prefix_schema_id=D,
        scopes=(scope(),),feature_names=('lag1',),feature_dtypes=('float64',),rows=((1.,),))


def test_duplicate_point_keys_reject():
    p=PredictionPoint(scope=scope(),value=0.)
    with pytest.raises(ValueError):PredictionBatch(request_id=D,points=(p,p))


def test_direct_event_has_no_invented_recursive_parents():
    data=dict(strategy='direct_multi_horizon',dataset_binding=binding(),scope=scope(horizon_step=3),request_id=D,
        predictor_id='synthetic',predictor_version='1',predictor_digest=D,representation_schema_id=D,
        configuration_digest=D,state_id=D,prefix_schema_id=D,rollout_compatibility_id=D,numeric_policy_id=D,
        parent_feedback_ids=(),value=-0.)
    assert PredictionEvent(**data).parent_feedback_ids==()
    data['parent_feedback_ids']=(D,D)
    with pytest.raises(ValueError):PredictionEvent(**data)


@pytest.mark.parametrize('purpose',['production_training','offline_forecast_evaluation'])
def test_additive_explicit_publication_vocabulary(purpose):
    ctx=DatasetAccessContext(binding().reference,UUID(int=6),UUID(int=7),'mapping','1.0.0',D,
        'sales','general','source','connection',purpose)
    assert ctx.purpose==purpose
    with pytest.raises(ValueError):DatasetAccessContext(ctx.reference,ctx.source_id,ctx.ingestion_batch_id,ctx.mapping_id,
        ctx.mapping_version,D,ctx.metric,ctx.business_scope,ctx.source_scope,ctx.connection_reference,'evaluation')
