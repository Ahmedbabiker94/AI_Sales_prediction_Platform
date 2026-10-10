"""Generic admission has no source, dataset, or warning-policy defaults."""

from dataclasses import replace

import pytest

from src.data.processing.quality import (
    QualityAction, QualityIssue, QualityIssueType, QualityPolicy, QualityRule,
    QualitySeverity,
)
from src.data.processing.training_admission import (
    TrainingAdmissionError, WarningAuthorization, enforce_training_admission,
    evaluate_training_admission,
)


def policy(action=QualityAction.KEEP):
    return QualityPolicy(policy_id="REFERENCE", rules=[
        QualityRule(rule_id="NEGATIVE", issue_code="NEGATIVE_VALUE", action=action,
                    parameters={"review_required": True}),
    ])


def warning():
    return QualityIssue(
        issue_code="NEGATIVE_VALUE", issue_type=QualityIssueType.INVALID,
        severity=QualitySeverity.WARNING, field="amount", message="Negative value",
        details={"metric": "demand", "source_type": "database"},
    )


def authorization():
    return WarningAuthorization(
        execution_context="reference_training", issue_code="NEGATIVE_VALUE",
        field="amount", metric="demand", action=QualityAction.KEEP,
    )


def test_generic_warning_is_blocked_without_explicit_authorization():
    issues = [warning()]
    result = evaluate_training_admission(issues, policy(), execution_context="reference_training")
    assert not result.metadata["training_admitted"]
    assert result.metadata["approved_warning_exceptions"] == []
    with pytest.raises(TrainingAdmissionError):
        enforce_training_admission(issues, result)


def test_explicit_authorization_is_observable_and_non_mutating():
    issues = [warning()]
    selected_policy = policy()
    before_issue = issues[0].model_dump()
    before_policy = selected_policy.model_dump()
    result = evaluate_training_admission(
        issues, selected_policy, execution_context="reference_training",
        warning_authorizations=(authorization(),),
    )
    assert result.metadata["training_admitted"]
    assert result.metadata["training_context"] == "reference_training"
    assert result.metadata["approved_warning_exceptions"] == ["NEGATIVE_VALUE"]
    assert issues[0].model_dump() == before_issue
    assert selected_policy.model_dump() == before_policy


@pytest.mark.parametrize("field,value", [
    ("execution_context", "different_training"), ("issue_code", "OTHER"),
    ("field", "another_amount"), ("metric", "another_metric"),
    ("action", QualityAction.QUARANTINE),
])
def test_authorization_requires_exact_scope(field, value):
    result = evaluate_training_admission(
        [warning()], policy(), execution_context="reference_training",
        warning_authorizations=(replace(authorization(), **{field: value}),),
    )
    assert not result.metadata["training_admitted"]


@pytest.mark.parametrize("action", [a for a in QualityAction if a is not QualityAction.KEEP])
def test_authorization_cannot_execute_or_bypass_actions(action):
    result = evaluate_training_admission(
        [warning()], policy(action), execution_context="reference_training",
        warning_authorizations=(authorization(),),
    )
    assert not result.metadata["training_admitted"]


@pytest.mark.parametrize("severity", [QualitySeverity.ERROR, QualitySeverity.CRITICAL])
def test_authorization_does_not_cover_blocking_severity(severity):
    issue = warning()
    issue.severity = severity
    result = evaluate_training_admission(
        [issue], policy(), execution_context="reference_training",
        warning_authorizations=(authorization(),),
    )
    assert not result.metadata["training_admitted"]


def test_generic_admission_has_no_implicit_walmart_exception():
    issue = warning()
    issue.field = "Weekly_Sales"
    issue.details = {"metric": "sales", "source_type": "csv"}
    result = evaluate_training_admission([issue], policy(), execution_context="walmart")
    assert not result.metadata["training_admitted"]


def test_rejection_message_has_fields_counts_and_no_raw_details():
    issues = [QualityIssue(
        issue_code='INVALID_NUMERIC', issue_type=QualityIssueType.INVALID,
        severity=QualitySeverity.ERROR, field=name, affected_count=count,
        message='raw-sensitive-value', details={'raw': 'sensitive-secret'},
    ) for name, count in [('Temperature', 2), ('Fuel_Price', 1)]]
    selected = QualityPolicy(policy_id='REJECTION', rules=[QualityRule(
        rule_id='INVALID', issue_code='INVALID_NUMERIC', action=QualityAction.QUARANTINE,
    )])
    result = evaluate_training_admission(issues, selected, execution_context='reference_training')
    with pytest.raises(TrainingAdmissionError) as exc:
        enforce_training_admission(issues, result)
    message = str(exc.value)
    assert 'INVALID_NUMERIC field=Fuel_Price affected=1' in message
    assert 'INVALID_NUMERIC field=Temperature affected=2' in message
    assert 'sensitive' not in message
    assert exc.value.issues is issues
    assert exc.value.result is result
    assert str(TrainingAdmissionError(list(reversed(issues)), result)) == message


def test_large_rejection_message_is_bounded_and_retains_all_evidence():
    issues = [QualityIssue(
        issue_code='INVALID_NUMERIC', issue_type=QualityIssueType.INVALID,
        severity=QualitySeverity.ERROR, field=str(i)+'x'*120,
        affected_count=i, message='sensitive-value',
    ) for i in range(50)]
    result = evaluate_training_admission(issues, policy(), execution_context='context'*100)
    error = TrainingAdmissionError(issues, result)
    assert len(str(error)) <= 1024
    assert 'truncated' in str(error)
    assert 'sensitive-value' not in str(error)
    assert error.issues is issues and error.result is result
