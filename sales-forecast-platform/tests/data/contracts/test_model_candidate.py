"""Candidate metadata is immutable data, not permission to load native code."""
from dataclasses import FrozenInstanceError
import ctypes
from decimal import Decimal
import numpy as np
import pandas as pd
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


@pytest.mark.parametrize('values',[
    [True,1.5], [False,2], [np.bool_(True),1.5], [np.bool_(False),2],
    [[1.5,True],[2.,3.]], ((1.5,2.),(np.bool_(False),3.)),
    [[[1.5,np.bool_(True)]]], [np.array(True),1.5],
    [np.array([False]),np.array([1.5])], True, np.bool_(False),
    np.array(True), np.array([[True,False]]),
    [pd.Index([True]),np.array([1.5])],
    [pd.Series([False]),np.array([1.5])],
    [pd.DataFrame([[True]]),np.array([[1.5]])],
    [(ctypes.c_bool*1)(True),np.array([1.5])], memoryview(np.array([True])),
])
def test_boolean_scalar_identities_reject_before_numpy_coercion(values):
    with pytest.raises(ValueError):strict_float32(values)


@pytest.mark.parametrize('values',[
    1.5, [1,1.5,-0.], ((1.5,-2.),(0.,-0.)), [[[1.5,-0.]]],
    np.array([[-2.,-0.],[0.,1.5]],dtype=np.float64), range(3),
    memoryview(np.array([1.5,-0.],dtype='>f8')),
    [memoryview(np.array([1.5],dtype='>f8')),np.array([-0.])],
])
def test_valid_numeric_shapes_and_signed_zero_are_preserved(values):
    original=np.asarray(values,dtype=np.float64)
    result=strict_float32(values)
    assert result.shape==original.shape and result.dtype==np.float32
    np.testing.assert_array_equal(result.astype(np.float64),original)
    np.testing.assert_array_equal(np.signbit(result),np.signbit(original))


def test_cyclic_raw_sequence_rejects_without_hanging():
    values=[1.5];values.append(values)
    with pytest.raises(ValueError):strict_float32(values)
