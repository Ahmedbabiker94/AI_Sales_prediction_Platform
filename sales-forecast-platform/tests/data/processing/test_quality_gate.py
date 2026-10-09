
from src.data.processing.quality import (
    QualityAction,
    QualityConditions,
    QualityGateDecision,
    QualityIssue,
    QualityIssueType,
    QualityPolicy,
    QualityRule,
    QualitySeverity,
)
from src.data.processing.quality_gate import QualityGate
from src.data.processing.sales_quality import (
    build_general_sales_quality_policy,
)


def make_issue(
    issue_code: str,
    *,
    severity: QualitySeverity = QualitySeverity.WARNING,
    issue_type: QualityIssueType = QualityIssueType.INVALID,
    field: str | None = None,
    affected_count: int = 1,
    details: dict | None = None,
) -> QualityIssue:
    return QualityIssue(
        issue_code=issue_code,
        issue_type=issue_type,
        severity=severity,
        field=field,
        affected_count=affected_count,
        message=f"Detected issue: {issue_code}",
        details=details or {},
    )


def make_policy(
    issue_code: str,
    action: QualityAction,
    *,
    enabled: bool = True,
    conditions: QualityConditions | None = None,
    parameters: dict | None = None,
) -> QualityPolicy:
    return QualityPolicy(
        policy_id="TEST_POLICY",
        rules=[
            QualityRule(
                rule_id=f"RULE_{issue_code}",
                issue_code=issue_code,
                action=action,
                enabled=enabled,
                conditions=conditions or QualityConditions(),
                parameters=parameters or {},
            )
        ],
    )


def test_gate_passes_when_no_issues():
    result = QualityGate.evaluate(
        [],
        build_general_sales_quality_policy(),
    )

    assert result.decision is QualityGateDecision.PASS
    assert result.metadata["issues_evaluated"] == 0


def test_gate_passes_with_informational_keep_issue():
    issue = make_issue(
        "ZERO_VALUE",
        severity=QualitySeverity.INFO,
    )
    policy = make_policy("ZERO_VALUE", QualityAction.KEEP)

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.PASS
    assert result.issue_codes == ["ZERO_VALUE"]


def test_gate_returns_warning_for_sales_anomaly():
    issue = make_issue(
        "ANOMALY_DETECTED",
        issue_type=QualityIssueType.ANOMALY,
        severity=QualitySeverity.WARNING,
        field="sales",
        affected_count=4,
        details={"metric": "sales"},
    )

    result = QualityGate.evaluate(
        [issue],
        build_general_sales_quality_policy(),
    )

    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert "SALES_ANOMALY_DETECTED" in result.metadata["matched_rule_ids"]
    assert "ANOMALY_DETECTED" in result.metadata[
        "review_required_issue_codes"
    ]
    assert result.metadata["gate_executes_actions"] is False


def test_gate_returns_warning_for_quarantine_action():
    issue = make_issue("MISSING_DIMENSION")
    policy = make_policy(
        "MISSING_DIMENSION",
        QualityAction.QUARANTINE,
    )

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert result.metadata["action_counts"]["quarantine"] == 1


def test_gate_rejects_explicit_reject_rule():
    issue = make_issue("SCHEMA_FAILURE")
    policy = build_general_sales_quality_policy()

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.REJECT
    assert "SCHEMA_FAILURE matched a REJECT rule" in result.reason


def test_gate_rejects_critical_issue_even_when_rule_says_keep():
    issue = make_issue(
        "ZERO_VALUE",
        severity=QualitySeverity.CRITICAL,
    )
    policy = make_policy("ZERO_VALUE", QualityAction.KEEP)

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.REJECT
    assert "CRITICAL severity" in result.reason


def test_gate_warns_for_unknown_issue_code():
    issue = make_issue(
        "NEW_UNREGISTERED_ISSUE",
        severity=QualitySeverity.INFO,
    )
    policy = build_general_sales_quality_policy()

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert result.metadata["unmatched_issues"][0]["issue_code"] == (
        "NEW_UNREGISTERED_ISSUE"
    )


def test_gate_does_not_apply_disabled_rule():
    issue = make_issue(
        "ZERO_VALUE",
        severity=QualitySeverity.INFO,
    )
    policy = make_policy(
        "ZERO_VALUE",
        QualityAction.KEEP,
        enabled=False,
    )

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert result.metadata["matched_rule_ids"] == []
    assert result.metadata["disabled_rule_ids"] == ["RULE_ZERO_VALUE"]


def test_gate_requires_conditions_to_match():
    issue = make_issue(
        "MISSING_VALUE",
        severity=QualitySeverity.INFO,
        field="inventory",
        details={"metric": "inventory"},
    )
    policy = make_policy(
        "MISSING_VALUE",
        QualityAction.IMPUTE,
        conditions=QualityConditions(metric="sales"),
    )

    result = QualityGate.evaluate([issue], policy)

    assert result.decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert result.metadata["matched_rule_ids"] == []
    assert result.metadata["unmatched_issues"][0]["reason"] == (
        "no enabled rule matched the issue conditions"
    )
