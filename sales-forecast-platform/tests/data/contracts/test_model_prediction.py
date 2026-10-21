"""Label-free D3-B data declarations are not issued execution credentials."""
import runpy
import struct
from dataclasses import FrozenInstanceError
from uuid import uuid4
import traceback
import warnings

import numpy as np
import pytest

from src.data.contracts.backtesting import ForecastScope, PredictionPoint
from src.data.contracts.model_evaluation import EvaluationDeclarationError
from src.data.contracts.model_prediction import FittedH1Prediction
from src.services.canonical_model_prediction_service import EvaluationUse


@pytest.fixture(scope='module')
def scope(tmp_path_factory):
    ns=runpy.run_path('tests/data/contracts/test_model_evaluation.py')
    data=ns['metadata'].__wrapped__(tmp_path_factory);d=ns['declare'](data)
    p=d.coverage.periods[0];r=d.relationship
    return ForecastScope(plan_digest=d.candidate.training_plan_digest,dataset_version=r.evaluation.reference.version_id,
        fold_id=d.coverage.fold_id,organization_id=r.evaluation.reference.organization_id,series_id=r.series[0].series_id,
        metric=r.metric,unit=r.unit,currency=r.currency,frequency=r.frequency,timezone=r.timezone,
        dimensions=r.series[0].dimensions,forecast_origin=p.forecast_origin,horizon_step=1,
        target_period=p.target_period,target_period_end=p.target_period_end)


@pytest.mark.parametrize('value',[-2.,0.,-0.,1.5,6.,float(np.float32(0.1))])
def test_exact_widening_and_signed_zero(scope,value):
    event=FittedH1Prediction(point=PredictionPoint(scope=scope,value=value),request_id='1'*64,
        history_identity='2'*64,representation_schema_id='3'*64,native_float32_hex=struct.pack('<f',value).hex())
    other=FittedH1Prediction.model_validate_json(event.model_dump_json())
    assert struct.pack('>d',other.point.value)==struct.pack('>d',value)
    with pytest.raises(ValueError):event.request_id='4'*64


@pytest.mark.parametrize('value',[0.1,float('nan'),float('inf'),float('-inf'),1e9])
def test_non_native_numbers_reject(scope,value):
    with pytest.raises(ValueError):
        FittedH1Prediction(point=PredictionPoint(scope=scope,value=value),request_id='1'*64,
            history_identity='2'*64,representation_schema_id='3'*64,native_float32_hex='00000000')


def test_public_prediction_declaration_diagnostics(scope,capsys,caplog):
    secret='D3B_'+uuid4().hex
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as error:FittedH1Prediction.model_validate({secret:secret})
    e=error.value;out=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.err+out.out+caplog.text
    assert e.__context__ is None and e.__cause__ is None and not seen


def test_handle_copy_is_not_a_transfer_api():
    import copy,pickle
    handle=EvaluationUse()
    for operation in (copy.copy,copy.deepcopy,pickle.dumps):
        with pytest.raises(TypeError):operation(handle)


def test_malformed_result_identity_is_sanitized(capsys,caplog):
    from src.data.contracts.model_prediction import FittedH1PredictionResult
    secret='IDENTITY_'+uuid4().hex
    malformed=FittedH1PredictionResult.model_construct(events=({secret:secret},))
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always')
        with pytest.raises(EvaluationDeclarationError) as error:malformed.prediction_id
    e=error.value;out=capsys.readouterr()
    assert secret not in str(e)+repr(e)+repr(e.args)+''.join(traceback.format_exception(e))+out.err+out.out+caplog.text
    assert e.__context__ is None and e.__cause__ is None and not seen
