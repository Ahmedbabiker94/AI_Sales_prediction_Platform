"""Immutable D1 audit declarations grant neither fitting permission nor trust."""
from datetime import timedelta
import runpy
from uuid import UUID

import pytest

from src.data.contracts.dataset import operational_json
from src.data.contracts.model_training import TrainingHandoffScope, TrainingLabelClaim


@pytest.fixture
def binding():
    return runpy.run_path('tests/data/contracts/test_temporal_preparation.py')['binding']()


def scope(binding,**changes):
    return TrainingHandoffScope(**dict(dict(scope_id='operator-owned',datasets=(binding,),
        configuration_digest='e'*64,label_authority_id='source-label-ledger',label_authority_version='synthetic-1'),**changes))


def test_operator_scope_frozen_roundtrip_and_identity(binding):
    s=scope(binding)
    assert TrainingHandoffScope.model_validate_json(s.model_dump_json())==s
    with pytest.raises(ValueError):s.datasets=()
    with pytest.raises(ValueError):s.datasets[0].mapping_id='different'
    assert scope(binding,label_authority_version='synthetic-2').digest!=s.digest
    assert scope(binding,max_handoff_bytes=10).digest!=s.digest


@pytest.mark.parametrize('changes',[
    {'purpose':'offline_forecast_evaluation'},{'version':'2.0'},{'datasets':()},
    {'datasets':None},{'label_authority_id':' '},{'configuration_digest':'forged'},
    {'max_verification_calls':True},{'max_verification_calls':10001},
    {'max_handoff_bytes':0},{'max_verification_claims':-1},{'max_verification_bytes':1.1},
])
def test_invalid_operator_scope_rejects(binding,changes):
    with pytest.raises(ValueError):scope(binding,**changes)


def claim(binding,**changes):
    old=runpy.run_path('tests/data/contracts/test_temporal_preparation.py');o=old['date']('2020-02-03');end=o+timedelta(days=7)
    return TrainingLabelClaim(**dict(dict(dataset=binding,plan_digest='e'*64,fold_id='fold-1',series_id=UUID(int=5),
        dimensions=(('region','north'),),metric='sales',unit='currency',currency='USD',frequency='weekly',timezone='UTC',
        forecast_origin=o,period_start=o,period_end=end,available_at=end,model_fit_cutoff=end+timedelta(days=7),
        source_record_id='4',lineage_id=UUID(int=8),evidence_reference='source-release-4',
        original_value_identity_json=operational_json(('decimal',1,(0,),-4))),**changes))


def test_training_label_M_not_O_and_exact_signed_scale_identity(binding):
    c=claim(binding)
    assert c.available_at>c.forecast_origin and c.available_at<=c.model_fit_cutoff
    assert TrainingLabelClaim.model_validate_json(c.model_dump_json())==c
    assert c.claim_id!=claim(binding,original_value_identity_json=operational_json(('decimal',0,(0,),-4))).claim_id
    assert c.claim_id!=claim(binding,original_value_identity_json=operational_json(('decimal',1,(0,),0))).claim_id
    with pytest.raises(ValueError):c.model_fit_cutoff=c.forecast_origin


@pytest.mark.parametrize('changes',[
    {'purpose':'diagnostic'},{'protocol':'captured_value_as_of_E_v1'},{'series_id':UUID(int=9)},
    {'dimensions':(('x','a'),('x','b'))},{'dimensions':(('z','a'),('a','b'))},
    {'original_value_identity_json':'["decimal",true,[0],-4]'},
    {'original_value_identity_json':'["decimal",0,[10],0]'},
    {'original_value_identity_json':'["integer","01"]'},
    {'original_value_identity_json':'["integer",1]'},
    {'original_value_identity_json':'["float64","7ff0000000000000"]'},
    {'original_value_identity_json':'["float64","7ff8000000000000"]'},
    {'original_value_identity_json':'["boolean",true]'},
    {'original_value_identity_json':'[ "integer", "1" ]'},
    {'original_value_identity_json':'null'},{'source_record_id':'Bearer private'},
])
def test_malformed_or_ambiguous_label_identity_rejects(binding,changes):
    with pytest.raises(ValueError):claim(binding,**changes)


@pytest.mark.parametrize('field',['available_at','model_fit_cutoff'])
def test_label_released_or_assessed_before_completion_rejects(binding,field):
    c=claim(binding)
    with pytest.raises(ValueError):claim(binding,**{field:c.forecast_origin})
