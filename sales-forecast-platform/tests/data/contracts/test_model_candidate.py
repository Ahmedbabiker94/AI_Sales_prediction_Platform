"""Candidate metadata is immutable data, not permission to load native code."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from src.data.contracts.model_candidate import OfflineXGBoostConfiguration,TrainingInvocation,CandidateBundle,strict_float32


@pytest.mark.parametrize('field,value',[
 ('horizon',2),('horizon',True),('model_family','lightgbm'),('serialization','pickle'),('serialization','json'),
 ('threads',2),('estimators',0),('estimators',17),('max_depth',4),('seed',True),('max_rows',513),
 ('max_columns',33),('max_cells',8193),('max_elapsed_seconds',61),('max_evidence_records',513),
 ('comparison_policy','allclose'),('input_policy','approximate'),('production_alias','current')])
def test_unsupported_configuration_is_not_silently_enabled(field,value):
    with pytest.raises(ValueError):OfflineXGBoostConfiguration(**{field:value})


def test_fixed_parameters_and_frozen_configuration():
    c=OfflineXGBoostConfiguration()
    assert c.parameters['device']=='cpu' and c.parameters['nthread']==1
    assert c.parameters['objective']=='reg:squarederror'
    with pytest.raises(ValueError):c.threads=3
    p=c.parameters;p['device']='cuda';assert c.parameters['device']=='cpu'


@pytest.mark.parametrize('value',[float('inf'),float('nan'),1e20,2**24+1,0.1])
def test_model_float32_ingress_rejects_loss_nonfinite_or_large_values(value):
    with pytest.raises(ValueError):strict_float32([value])


def test_model_float32_signed_and_zero_are_preserved():
    import numpy as np
    x=strict_float32([-2.,-0.,0.,1.5])
    assert x.dtype==np.float32 and np.signbit(x[1]) and not np.signbit(x[2])


@pytest.mark.parametrize('payload',[b'',b'{}',b'not json',b'{}\n',b'null',b'[]',b'x'*(2*1024*1024+1)])
def test_invalid_or_unbounded_bundle_bytes_reject_without_model_loading(payload):
    b=CandidateBundle(payload)
    with pytest.raises((ValueError,TypeError)):b.validate()
    with pytest.raises(FrozenInstanceError):b.payload=b'new'


@pytest.mark.parametrize('value',[Decimal('1E-400'),Decimal('0.1'),True,'1'])
def test_raw_non_model_scalars_are_not_silently_coerced(value):
    with pytest.raises(ValueError):strict_float32([value])
