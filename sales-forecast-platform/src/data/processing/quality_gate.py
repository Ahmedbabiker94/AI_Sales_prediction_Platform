
"""Policy-based quality gate decision engine.

The gate evaluates quality issues against a policy. It does not execute
repairs, imputations, normalization, deduplication, or quarantine actions.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from src.data.processing.quality import (
    QualityAction,
    QualityGateDecision,
    QualityGateResult,
    QualityIssue,
    QualityPolicy,
    QualityRule,
    QualitySeverity,
)


def _normalize_value(value: Any) -> str | None:
    """Normalize enum/string values for condition comparisons."""
    if value is None:
        return None

    if isinstance(value, Enum):
        value = value.value

    return str(value).strip().casefold()


def _conditions_match(
    issue: QualityIssue,
    rule: QualityRule,
    context: Mapping[str, Any],
) -> bool:
    """Return whether the issue satisfies every condition defined by a rule."""
    conditions = rule.conditions

    if conditions.field is not None:
        if _normalize_value(issue.field) != _normalize_value(
            conditions.field
        ):
            return False

    if conditions.metric is not None:
        actual_metric = issue.details.get(
            "metric",
            context.get("metric"),
        )
        if _normalize_value(actual_metric) != _normalize_value(
            conditions.metric
        ):
            return False

    if conditions.source_type is not None:
        actual_source_type = issue.details.get(
            "source_type",
            context.get("source_type"),
        )
        if _normalize_value(actual_source_type) != _normalize_value(
            conditions.source_type
        ):
            return False

    return True


def _unique(values: Sequence[str]) -> list[str]:
    """Preserve order while removing duplicates."""
    return list(dict.fromkeys(values))


class QualityGate:
    """Evaluate quality issues against a configured quality policy."""

    @staticmethod
    def evaluate(
        issues: Sequence[QualityIssue],
        policy: QualityPolicy,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> QualityGateResult:
        """Return a gate decision without modifying data or executing actions.

        Conditions use issue.field, issue.details, and optional context.
        For example, a metric condition can be supplied through
        issue.details["metric"] or context["metric"].
        """
        reported_issues = list(issues)
        evaluation_context = context or {}

        issue_codes: list[str] = []
        matched_rule_ids: list[str] = []
        disabled_rule_ids: list[str] = []
        unmatched_issues: list[dict[str, str]] = []
        policy_actions: list[dict[str, Any]] = []
        review_required_codes: list[str] = []

        action_counts: Counter[str] = Counter()
        severity_counts: Counter[str] = Counter()

        rejection_reasons: list[str] = []
        has_warnings = False
        affected_count_total = sum(
            issue.affected_count for issue in reported_issues
        )

        for issue in reported_issues:
            issue_codes.append(issue.issue_code)
            severity_counts[issue.severity.value] += 1

            code_rules = [
                rule
                for rule in policy.rules
                if rule.issue_code == issue.issue_code
            ]

            disabled_rule_ids.extend(
                rule.rule_id
                for rule in code_rules
                if not rule.enabled
            )

            applicable_rules = [
                rule
                for rule in code_rules
                if rule.enabled
                and _conditions_match(
                    issue,
                    rule,
                    evaluation_context,
                )
            ]

            # Critical issues are rejected regardless of their matched action.
            if issue.severity is QualitySeverity.CRITICAL:
                rejection_reasons.append(
                    f"{issue.issue_code} has CRITICAL severity"
                )

            # Unknown, disabled, or condition-mismatched issues must not
            # silently pass as clean.
            if not applicable_rules:
                has_warnings = True

                if not code_rules:
                    reason = "no rule defined for this issue code"
                elif not any(rule.enabled for rule in code_rules):
                    reason = "all rules for this issue code are disabled"
                else:
                    reason = "no enabled rule matched the issue conditions"

                unmatched_issues.append(
                    {
                        "issue_code": issue.issue_code,
                        "reason": reason,
                        "severity": issue.severity.value,
                    }
                )

            issue_actions: list[str] = []
            issue_rule_ids: list[str] = []
            issue_review_required = False

            for rule in applicable_rules:
                matched_rule_ids.append(rule.rule_id)
                issue_rule_ids.append(rule.rule_id)

                action_value = rule.action.value
                issue_actions.append(action_value)

                issue_review_required = (
                    issue_review_required
                    or bool(rule.parameters.get("review_required", False))
                )

            unique_issue_actions = _unique(issue_actions)

            if unique_issue_actions:
                policy_actions.append(
                    {
                        "issue_code": issue.issue_code,
                        "rule_ids": _unique(issue_rule_ids),
                        "actions": unique_issue_actions,
                        "affected_count": issue.affected_count,
                    }
                )

                for action_value in unique_issue_actions:
                    action_counts[action_value] += 1

            # An explicit REJECT action always wins.
            if QualityAction.REJECT.value in unique_issue_actions:
                rejection_reasons.append(
                    f"{issue.issue_code} matched a REJECT rule"
                )

            if (
                issue.severity
                in (QualitySeverity.WARNING, QualitySeverity.ERROR)
                or issue_review_required
            ):
                has_warnings = True

            # Non-KEEP actions indicate work defined by policy. The gate
            # records that work but never executes it.
            non_keep_actions = [
                action
                for action in unique_issue_actions
                if action != QualityAction.KEEP.value
                and action != QualityAction.REJECT.value
            ]

            if non_keep_actions:
                has_warnings = True

            if issue_review_required:
                review_required_codes.append(issue.issue_code)

        if rejection_reasons:
            decision = QualityGateDecision.REJECT
            reason = (
                "The quality policy rejected the dataset: "
                + "; ".join(_unique(rejection_reasons))
                + "."
            )
        elif has_warnings:
            decision = QualityGateDecision.PASS_WITH_WARNINGS
            reason = (
                "Non-fatal quality issues require review, handling, "
                "or a policy decision."
            )
        else:
            decision = QualityGateDecision.PASS
            reason = (
                "No quality issues were reported, or all reported "
                "issues are informational and allowed by policy."
            )

        return QualityGateResult(
            decision=decision,
            reason=reason,
            issue_codes=_unique(issue_codes),
            metadata={
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "issues_evaluated": len(reported_issues),
                "affected_count_total": affected_count_total,
                "severity_counts": dict(severity_counts),
                "action_counts": dict(action_counts),
                "matched_rule_ids": _unique(matched_rule_ids),
                "disabled_rule_ids": _unique(disabled_rule_ids),
                "unmatched_issues": unmatched_issues,
                "policy_actions": policy_actions,
                "review_required_issue_codes": _unique(
                    review_required_codes
                ),
                "gate_executes_actions": False,
            }
        )
