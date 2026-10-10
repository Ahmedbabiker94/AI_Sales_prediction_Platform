"""Generic policy evaluation and fail-closed training admission."""

from collections.abc import Sequence
from dataclasses import dataclass

from src.data.processing.quality import (
    QualityAction, QualityGateDecision, QualityGateResult, QualityIssue,
    QualityPolicy, QualitySeverity,
)
from src.data.processing.quality_gate import QualityGate


@dataclass(frozen=True)
class WarningAuthorization:
    """Explicit authorization for one warning in one execution context.

    Only matched KEEP warnings may be authorized. This does not authorize
    repairs, unmatched issues, ERROR/CRITICAL issues, or other contexts.
    """

    execution_context: str
    issue_code: str
    field: str
    metric: str
    action: QualityAction


class TrainingAdmissionError(ValueError):
    """Rejected input, with observable issues and admission evidence."""

    def __init__(self, issues, result):
        self.issues = issues
        self.result = result
        blocking = set(result.metadata.get("blocking_issue_codes", ()))
        summaries = sorted({
            (issue.issue_code, issue.field or "<dataset>", issue.affected_count)
            for issue in issues if issue.issue_code in blocking
        })
        context = str(result.metadata.get("training_context", "unspecified"))[:64]
        parts = [
            f"{code[:128]} field={field[:128]} affected={count}"
            for code, field, count in summaries[:8]
        ]
        if len(summaries) > 8:
            parts.append(f"{len(summaries) - 8} additional issue summaries")
        if not parts:
            parts.append("missing admission evidence or rejected gate decision")
        message = f"Training admission rejected ({context}): " + "; ".join(parts)
        suffix = " ... (summary truncated; see .issues and .result)"
        if len(message) > 1024:
            message = message[:1024 - len(suffix)] + suffix
        super().__init__(message)



def evaluate_training_admission(
    issues: list[QualityIssue],
    policy: QualityPolicy,
    *,
    execution_context: str,
    warning_authorizations: Sequence[WarningAuthorization] = (),
) -> QualityGateResult:
    """Evaluate policy once; enforce stricter admission without executing actions."""
    result = QualityGate.evaluate(issues, policy)
    actions = result.metadata["policy_actions"]
    unmatched = {item["issue_code"] for item in result.metadata["unmatched_issues"]}
    review = set(result.metadata["review_required_issue_codes"])
    blocking = []
    exceptions = []
    for issue in issues:
        matched_actions = {
            action for item in actions if item["issue_code"] == issue.issue_code
            for action in item["actions"]
        }
        authorized = (
            issue.severity is QualitySeverity.WARNING
            and matched_actions == {QualityAction.KEEP.value}
            and issue.issue_code not in unmatched
            and any(
                authorization.execution_context == execution_context
                and authorization.issue_code == issue.issue_code
                and authorization.field == issue.field
                and authorization.metric == issue.details.get("metric")
                and authorization.action is QualityAction.KEEP
                for authorization in warning_authorizations
            )
        )
        allowed = (
            matched_actions == {QualityAction.KEEP.value}
            and issue.issue_code not in unmatched
            and issue.severity is QualitySeverity.INFO
            and issue.issue_code not in review
        )
        if authorized:
            exceptions.append(issue.issue_code)
        elif not allowed:
            blocking.append(issue.issue_code)
    admitted = result.decision is not QualityGateDecision.REJECT and not blocking
    result.metadata.update({
        "training_admitted": admitted,
        "blocking_issue_codes": list(dict.fromkeys(blocking)),
        "approved_warning_exceptions": list(dict.fromkeys(exceptions)),
        "training_context": execution_context,
    })
    return result


def enforce_training_admission(issues, result):
    """Stop before downstream work; never execute policy actions."""
    if not result.metadata.get("training_admitted", False) or result.decision is QualityGateDecision.REJECT:
        raise TrainingAdmissionError(issues, result)
