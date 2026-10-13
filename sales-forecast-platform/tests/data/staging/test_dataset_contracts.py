from dataclasses import FrozenInstanceError
import pytest
from src.data.contracts.dataset import ConsistencyEvidence, StagingBudgets, StagingState


@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_invalid_budgets(value):
    with pytest.raises(ValueError): StagingBudgets(max_rows=value)


def test_frozen_and_closed_states():
    with pytest.raises(FrozenInstanceError): StagingBudgets().max_rows = 3
    with pytest.raises(ValueError): StagingState('finalized')
    with pytest.raises(ValueError): StagingBudgets(max_concurrent_attempts=2)


@pytest.mark.parametrize('kwargs', [dict(kind='snapshot'), dict(kind='external_snapshot'),
    dict(kind='transaction_scoped', reference=' '), dict(reference='\ud800')])
def test_evidence_constraints(kwargs):
    with pytest.raises((ValueError, UnicodeError)): ConsistencyEvidence(**kwargs)


def test_reference_identity_validation():
    from uuid import UUID
    from src.data.contracts.dataset import StoredDatasetReference
    with pytest.raises(ValueError):StoredDatasetReference('org',UUID(int=1),UUID(int=2),'a'*64)
    with pytest.raises(ValueError):StoredDatasetReference(UUID(int=1),UUID(int=2),UUID(int=3),'finalized')
