"""General sales-domain quality rule catalog.

This module defines default *policy semantics* for common sales-data quality
cases. It does not execute repairs, imputations, anomaly detection, or
quarantine operations.
"""

from src.data.processing.quality import (
    QualityAction,
    QualityConditions,
    QualityIssueType,
    QualityPolicy,
    QualityRule,
)


SALES_GENERAL_POLICY_ID = "SALES_GENERAL"
SALES_GENERAL_POLICY_VERSION = "1.0.0"


def build_general_sales_quality_policy() -> QualityPolicy:
    """Build the generic sales quality policy with safe default actions.

    The catalog deliberately avoids assuming company-specific semantics.
    For example, negative sales are retained because they may represent
    legitimate returns/refunds/corrections, while detected anomalies are
    retained and flagged for contextual review.
    """

    rules = [
        # Missing data
        QualityRule(
            rule_id="SALES_MISSING_VALUE",
            issue_code="MISSING_VALUE",
            action=QualityAction.IMPUTE,
            conditions=QualityConditions(metric="sales"),
            parameters={
                "strategy": "time_interpolation",
                "bounded": True,
                "max_gap": 2,
            },
        ),
        QualityRule(
            rule_id="SALES_MISSING_REQUIRED_FIELD",
            issue_code="MISSING_REQUIRED_FIELD",
            action=QualityAction.REJECT,
        ),
        QualityRule(
            rule_id="SALES_MISSING_DIMENSION",
            issue_code="MISSING_DIMENSION",
            action=QualityAction.QUARANTINE,
        ),

        # Duplicates
        QualityRule(
            rule_id="SALES_DUPLICATE_RECORD",
            issue_code="DUPLICATE_RECORD",
            action=QualityAction.DEDUPLICATE,
            parameters={"match": "exact"},
        ),
        QualityRule(
            rule_id="SALES_BUSINESS_KEY_DUPLICATE",
            issue_code="BUSINESS_KEY_DUPLICATE",
            action=QualityAction.QUARANTINE,
            parameters={"resolution": "require_business_rule"},
        ),

        # Invalid / suspicious numeric values
        QualityRule(
            rule_id="SALES_INVALID_VALUE",
            issue_code="INVALID_VALUE",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_NEGATIVE_VALUE",
            issue_code="NEGATIVE_VALUE",
            action=QualityAction.KEEP,
            conditions=QualityConditions(metric="sales"),
            parameters={
                "review_required": True,
                "possible_business_meanings": [
                    "return",
                    "refund",
                    "correction",
                    "reversal",
                ],
            },
        ),
        QualityRule(
            rule_id="SALES_ZERO_VALUE",
            issue_code="ZERO_VALUE",
            action=QualityAction.KEEP,
            conditions=QualityConditions(metric="sales"),
            parameters={"review_required": False},
        ),
        QualityRule(
            rule_id="SALES_OUT_OF_RANGE",
            issue_code="OUT_OF_RANGE",
            action=QualityAction.QUARANTINE,
        ),

        # Date / period integrity
        QualityRule(
            rule_id="SALES_INVALID_DATE",
            issue_code="INVALID_DATE",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_FUTURE_DATE",
            issue_code="FUTURE_DATE",
            action=QualityAction.KEEP,
            parameters={"review_required": True},
        ),
        QualityRule(
            rule_id="SALES_INVALID_PERIOD",
            issue_code="INVALID_PERIOD",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_DUPLICATE_PERIOD",
            issue_code="DUPLICATE_PERIOD",
            action=QualityAction.QUARANTINE,
            parameters={"resolution": "require_series_level_rule"},
        ),

        # Numeric representation
        QualityRule(
            rule_id="SALES_INVALID_NUMERIC",
            issue_code="INVALID_NUMERIC",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_NUMERIC_FORMAT",
            issue_code="NUMERIC_FORMAT",
            action=QualityAction.NORMALIZE,
            parameters={"target_type": "numeric"},
        ),

        # Units / currencies
        QualityRule(
            rule_id="SALES_MISSING_CURRENCY",
            issue_code="MISSING_CURRENCY",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_INVALID_CURRENCY",
            issue_code="INVALID_CURRENCY",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_CURRENCY_MISMATCH",
            issue_code="CURRENCY_MISMATCH",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_MISSING_UNIT",
            issue_code="MISSING_UNIT",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_UNIT_MISMATCH",
            issue_code="UNIT_MISMATCH",
            action=QualityAction.QUARANTINE,
        ),

        # Dimensions / consistency
        QualityRule(
            rule_id="SALES_UNKNOWN_DIMENSION",
            issue_code="UNKNOWN_DIMENSION",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_INVALID_DIMENSION",
            issue_code="INVALID_DIMENSION",
            action=QualityAction.QUARANTINE,
        ),
        QualityRule(
            rule_id="SALES_BUSINESS_INCONSISTENCY",
            issue_code="BUSINESS_INCONSISTENCY",
            action=QualityAction.QUARANTINE,
        ),

        # Anomaly / outlier handling
        QualityRule(
            rule_id="SALES_ANOMALY_DETECTED",
            issue_code="ANOMALY_DETECTED",
            action=QualityAction.KEEP,
            conditions=QualityConditions(metric="sales"),
            parameters={
                "review_required": True,
                "treat_as_error": False,
                "possible_context_fields": [
                    "holiday",
                    "promotion",
                    "temperature",
                    "weather",
                    "external_event",
                ],
                "detectors": [
                    "statistical",
                    "time_series",
                    "contextual",
                ],
            },
        ),

        # Schema-level failures
        QualityRule(
            rule_id="SALES_SCHEMA_FAILURE",
            issue_code="SCHEMA_FAILURE",
            action=QualityAction.REJECT,
        ),
        QualityRule(
            rule_id="SALES_UNSUPPORTED_FIELD_TYPE",
            issue_code="UNSUPPORTED_FIELD_TYPE",
            action=QualityAction.REJECT,
        ),
    ]

    return QualityPolicy(
        policy_id=SALES_GENERAL_POLICY_ID,
        version=SALES_GENERAL_POLICY_VERSION,
        rules=rules,
    )
