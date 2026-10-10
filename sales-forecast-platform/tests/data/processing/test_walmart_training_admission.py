import pandas as pd
import pytest

from src.data.processing.quality import QualityAction, QualityGateDecision, QualityIssue, QualityIssueType, QualitySeverity
from src.data.processing.sales_quality import build_general_sales_quality_policy
from src.data.processing.training_admission import enforce_training_admission, TrainingAdmissionError
from src.data.processing.walmart_training_admission import admit_walmart_training, evaluate_walmart_training_admission
from src.pipelines.data_validation import validate_dataframe


def issue(code, severity=QualitySeverity.WARNING, field=None, details=None):
    return QualityIssue(issue_code=code, issue_type=QualityIssueType.INVALID, severity=severity, field=field, details=details or {}, message='test')


def negative():
    return issue('NEGATIVE_VALUE', field='Weekly_Sales', details={'metric':'sales'})


def test_negative_exception_observable_and_policy_unchanged():
    policy = build_general_sales_quality_policy(); original = policy.model_dump()
    result = evaluate_walmart_training_admission([negative()], policy)
    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert result.metadata['training_admitted']
    assert result.metadata['approved_warning_exceptions'] == ['NEGATIVE_VALUE']
    assert result.metadata['review_required_issue_codes'] == ['NEGATIVE_VALUE']
    assert policy.model_dump() == original


@pytest.mark.parametrize('action', [a for a in QualityAction if a is not QualityAction.KEEP])
def test_unexecuted_actions_fail_closed(action):
    policy = build_general_sales_quality_policy()
    rule = next(r for r in policy.rules if r.issue_code == 'ZERO_VALUE'); rule.action = action
    issues = [issue('ZERO_VALUE', QualitySeverity.INFO, 'Weekly_Sales', {'metric':'sales'})]
    result = evaluate_walmart_training_admission(issues, policy)
    with pytest.raises(TrainingAdmissionError): enforce_training_admission(issues, result)


@pytest.mark.parametrize('blocking', [issue('INVALID_DATE'), issue('UNRECOGNIZED'), issue('INVALID_VALUE', QualitySeverity.ERROR), issue('ANOMALY_DETECTED', details={'metric':'sales'})])
def test_negative_exception_cannot_bypass_other_issues(blocking):
    result = evaluate_walmart_training_admission([negative(), blocking])
    assert not result.metadata['training_admitted']
    assert blocking.issue_code in result.metadata['blocking_issue_codes']


@pytest.mark.parametrize('mutation', ['disabled', 'mismatch', 'critical'])
def test_negative_exception_is_narrow(mutation):
    policy = build_general_sales_quality_policy(); n = negative()
    rule = next(r for r in policy.rules if r.issue_code == 'NEGATIVE_VALUE')
    if mutation == 'disabled': rule.enabled = False
    elif mutation == 'mismatch': n.field = 'other'
    else: n.severity = QualitySeverity.CRITICAL
    assert not evaluate_walmart_training_admission([n], policy).metadata['training_admitted']


def test_validation_preserves_values_and_does_not_add_returns(caplog):
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    df['Weekly_Sales'] = [-10.5, 0.0, 42.0]
    before = df.copy(deep=True)
    assert validate_dataframe(df) is df
    assert 'negative Weekly_Sales' in caplog.text
    pd.testing.assert_frame_equal(df, before)
    assert 'Returns' not in df
    assert admit_walmart_training(df).metadata['training_admitted']


def test_negative_warning_logging_does_not_raise_under_warning_errors(caplog):
    import warnings
    df = pd.read_csv('data/walmart_cleaned.csv', nrows=3)
    df['Weekly_Sales'] = [-10.5, 0.0, 42.0]
    before = df.copy(deep=True)
    result = admit_walmart_training(df)
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        assert validate_dataframe(df) is df
    assert 'negative Weekly_Sales' in caplog.text
    assert result.metadata['approved_warning_exceptions'] == ['NEGATIVE_VALUE']
    pd.testing.assert_frame_equal(df, before)
