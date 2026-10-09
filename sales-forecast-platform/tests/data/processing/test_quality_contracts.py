from src.data.processing.market import SaudiMarketProfile
from src.data.processing.quality import (
    QualityAction, QualityConditions, QualityGateDecision, QualityGateResult,
    QualityIssue, QualityIssueType, QualityPolicy, QualityRule, QualitySeverity,
    SourceType,
)

def test_quality_issue_represents_missing_sales():
    issue = QualityIssue(
        issue_code="MISSING_VALUE", issue_type=QualityIssueType.MISSING,
        severity=QualitySeverity.WARNING, field="sales", affected_count=10,
        message="10 sales values are missing.",
    )
    assert issue.field == "sales"
    assert issue.affected_count == 10

def test_quality_rule_supports_structured_conditions_and_parameters():
    rule = QualityRule(
        rule_id="CSV_MISSING_SALES", issue_code="MISSING_VALUE",
        action=QualityAction.IMPUTE,
        conditions=QualityConditions(field="sales", metric="sales", source_type=SourceType.CSV),
        parameters={"strategy": "interpolation", "max_gap": 2},
    )
    assert rule.conditions.field == "sales"
    assert rule.conditions.metric == "sales"
    assert rule.conditions.source_type is SourceType.CSV
    assert rule.parameters["max_gap"] == 2

def test_quality_policy_contains_multiple_rules():
    policy = QualityPolicy(
        policy_id="SALES_DEFAULT", market_profile_code="SA",
        rules=[
            QualityRule(rule_id="MISSING_SALES", issue_code="MISSING_VALUE", action=QualityAction.IMPUTE),
            QualityRule(rule_id="DUPLICATE_SALES", issue_code="DUPLICATE_RECORD", action=QualityAction.DEDUPLICATE),
        ],
    )
    assert len(policy.rules) == 2
    assert policy.market_profile_code == "SA"

def test_saudi_market_profile():
    profile = SaudiMarketProfile()
    assert profile.market_code == "SA"
    assert profile.country_code == "SA"
    assert profile.locale == "ar-SA"
    assert profile.timezone == "Asia/Riyadh"
    assert "ar" in profile.supported_languages
    assert "en" in profile.supported_languages
    assert "hijri" in profile.supported_calendars


def test_saudi_market_profile_does_not_force_currency():
    profile = SaudiMarketProfile()

    assert "currency" not in SaudiMarketProfile.model_fields

    try:
        SaudiMarketProfile(currency="USD")
    except Exception:
        return

    raise AssertionError(
        "MarketProfile must not accept currency as a market-wide hard-coded field."
    )

def test_quality_gate_supports_three_decisions():
    assert QualityGateResult(decision=QualityGateDecision.PASS, reason="All checks passed.").decision is QualityGateDecision.PASS
    assert QualityGateResult(decision=QualityGateDecision.PASS_WITH_WARNINGS, reason="Some rows quarantined.").decision is QualityGateDecision.PASS_WITH_WARNINGS
    assert QualityGateResult(decision=QualityGateDecision.REJECT, reason="Required field is missing.").decision is QualityGateDecision.REJECT

def test_contracts_reject_unknown_fields():
    try:
        QualityRule(rule_id="X", issue_code="MISSING_VALUE", action=QualityAction.IMPUTE, unexpected_field=True)
    except Exception:
        return
    raise AssertionError("Unknown fields must be rejected.")


def test_general_sales_quality_policy_covers_core_sales_cases():
    from src.data.processing.sales_quality import build_general_sales_quality_policy

    policy = build_general_sales_quality_policy()
    issue_codes = {rule.issue_code for rule in policy.rules}

    expected = {
        "MISSING_VALUE",
        "MISSING_REQUIRED_FIELD",
        "MISSING_DIMENSION",
        "DUPLICATE_RECORD",
        "BUSINESS_KEY_DUPLICATE",
        "INVALID_VALUE",
        "NEGATIVE_VALUE",
        "ZERO_VALUE",
        "OUT_OF_RANGE",
        "INVALID_DATE",
        "FUTURE_DATE",
        "INVALID_PERIOD",
        "DUPLICATE_PERIOD",
        "INVALID_NUMERIC",
        "NUMERIC_FORMAT",
        "MISSING_CURRENCY",
        "INVALID_CURRENCY",
        "CURRENCY_MISMATCH",
        "MISSING_UNIT",
        "UNIT_MISMATCH",
        "UNKNOWN_DIMENSION",
        "INVALID_DIMENSION",
        "BUSINESS_INCONSISTENCY",
        "ANOMALY_DETECTED",
        "SCHEMA_FAILURE",
        "UNSUPPORTED_FIELD_TYPE",
    }

    assert expected.issubset(issue_codes)


def test_negative_sales_are_kept_by_default():
    from src.data.processing.sales_quality import build_general_sales_quality_policy

    policy = build_general_sales_quality_policy()
    rule = next(rule for rule in policy.rules if rule.issue_code == "NEGATIVE_VALUE")

    assert rule.action is QualityAction.KEEP
    assert rule.parameters["review_required"] is True
    assert "return" in rule.parameters["possible_business_meanings"]


def test_sales_anomaly_is_kept_and_context_flagged():
    from src.data.processing.sales_quality import build_general_sales_quality_policy

    policy = build_general_sales_quality_policy()
    rule = next(rule for rule in policy.rules if rule.issue_code == "ANOMALY_DETECTED")

    assert rule.action is QualityAction.KEEP
    assert rule.parameters["treat_as_error"] is False
    assert "temperature" in rule.parameters["possible_context_fields"]
    assert "holiday" in rule.parameters["possible_context_fields"]
    assert "promotion" in rule.parameters["possible_context_fields"]
    assert "external_event" in rule.parameters["possible_context_fields"]
