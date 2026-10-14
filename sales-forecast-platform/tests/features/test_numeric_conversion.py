"""Synthetic tolerance declarations only: no production business approval."""
from decimal import Decimal, localcontext
import math
import runpy

import pytest

from src.data.contracts.features import NumericConversionPolicy, FeatureConfiguration
from src.features.canonical_feature_pipeline import _number, CanonicalFeaturePipeline, FeatureExecutionError


def policy(**changes):
    data=dict(field='value',measurement=dict(metric='reported',unit='amount',currency='USD'),
        mode='BOUNDED_FLOAT64',absolute_tolerance='1e-12',relative_tolerance='0')
    data.update(changes)
    return NumericConversionPolicy(**data)


@pytest.mark.parametrize('value',[Decimal('0.1'),Decimal('19.99'),Decimal('-0.1'),Decimal('0.000001')])
def test_synthetic_explicit_bounds_accept_and_strict_rejects(value):
    with pytest.raises(FeatureExecutionError):_number(value)
    original=value.as_tuple()
    assert _number(value,policy())==float(value)
    assert value.as_tuple()==original


def test_error_boundary_is_exact_and_decimal_context_independent():
    with localcontext() as context:
        context.prec=200
        error=abs(Decimal.from_float(.1)-Decimal('0.1'))
        exact=policy(absolute_tolerance=error)
        insufficient=policy(absolute_tolerance=error/2)
    with localcontext() as context:
        context.prec=2
        assert _number(Decimal('0.1'),exact)==.1
        with pytest.raises(FeatureExecutionError):_number(Decimal('0.1'),insufficient)


@pytest.mark.parametrize('value',[Decimal('NaN'),Decimal('Infinity'),Decimal('-Infinity'),
    Decimal('1e400'),Decimal('1e-400'),float('nan'),float('inf'),True,'0.1',object()])
def test_unsafe_inputs_reject_even_with_large_tolerance(value):
    with pytest.raises(FeatureExecutionError):_number(value,policy(absolute_tolerance='100'))


def test_signed_zero_and_large_integer_rules():
    value=Decimal('-0.0000')
    assert math.copysign(1,_number(value,policy()))==-1 and value.as_tuple().sign==1
    assert _number(2**53)==float(2**53)
    with pytest.raises(FeatureExecutionError):_number(2**53+1)
    with pytest.raises(FeatureExecutionError):_number(2**53+1,policy())
    assert _number(2**53+1,policy(absolute_tolerance='1'))==float(2**53+1)


@pytest.mark.parametrize('changes',[
    {'absolute_tolerance':'-1'},{'relative_tolerance':'NaN'}, {'absolute_tolerance':.1},
    {'relative_tolerance':True},{'absolute_tolerance':'1e-1001'},
    {'version':'2.0'},{'field':' '},{'rounding':'floor'},{'underflow':'allow'},
    {'mode':'STRICT_EXACT','absolute_tolerance':'1'},
])
def test_invalid_tolerance_declarations(changes):
    with pytest.raises(ValueError):policy(**changes)


def configuration():
    return runpy.run_path('tests/features/conftest.py')['feature_config']()


def test_strict_serialization_and_executor_are_backward_compatible():
    config=configuration()
    assert 'numeric_policies' not in config.execution.model_dump()
    assert CanonicalFeaturePipeline(config).executor_version=='1.0.0'
    assert FeatureConfiguration.model_validate_json(config.model_dump_json())==config


def test_policy_changes_configuration_identity_and_executor_binding():
    old=configuration();values=old.model_dump()
    values['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[policy().model_dump()])
    new=FeatureConfiguration.model_validate(values)
    exact=CanonicalFeaturePipeline(old);bounded=CanonicalFeaturePipeline(new)
    assert exact.digest!=bounded.digest and bounded.executor_version=='1.1.0'
    assert bounded.convert_numeric(Decimal('19.99'))==19.99
    with pytest.raises(FeatureExecutionError):bounded.convert_numeric(Decimal('.1'),'product_class')
    changed=new.model_dump();changed['execution']['numeric_policies'][0]['absolute_tolerance']='1e-10'
    assert CanonicalFeaturePipeline(FeatureConfiguration.model_validate(changed)).digest!=bounded.digest


@pytest.mark.parametrize('fault',['missing','duplicate','field','unit','metric','category','currency'])
def test_numeric_policy_roles_and_measurement_must_match(fault):
    values=configuration().model_dump();p=policy().model_dump()
    if fault=='field':p['field']='undeclared'
    if fault=='category':p['field']='product_class'
    if fault=='unit':p['measurement']['unit']='items'
    if fault=='metric':p['measurement']['metric']='returns'
    if fault=='currency':p['measurement']['currency']='EUR'
    values['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[] if fault=='missing' else [p,p] if fault=='duplicate' else [p])
    with pytest.raises(ValueError):FeatureConfiguration.model_validate(values)


def test_covariate_and_target_have_independent_policies_and_units():
    values=configuration().model_dump();values['canonical_inputs']=(*values['canonical_inputs'],'price')
    values['execution']['exogenous']=[dict(field='price',availability='known_in_advance',missing='reject',
        measurement=dict(metric='reported',unit='unit_price',currency='USD'))]
    values['execution'].update(numeric_conversion='versioned_float64',numeric_policies=[
        policy(mode='STRICT_EXACT',absolute_tolerance='0').model_dump(),
        policy(field='price',measurement=dict(metric='reported',unit='unit_price',currency='USD')).model_dump()])
    engine=CanonicalFeaturePipeline(FeatureConfiguration.model_validate(values))
    with pytest.raises(FeatureExecutionError):engine.convert_numeric(Decimal('.1'),'value')
    assert engine.convert_numeric(Decimal('.1'),'price')==.1
