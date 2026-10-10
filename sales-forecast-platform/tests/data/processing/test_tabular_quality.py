"""Generic mechanisms use explicit non-Walmart fields and source context."""

import numpy as np
import pandas as pd
import pytest

from src.data.processing.quality import QualitySeverity
from src.data.processing.tabular_quality import detect_tabular_quality


def frame():
    return pd.DataFrame({
        "branch_id": [1.0, 1.0, 1.0], "sku_id": [2.0, 2.0, 2.0],
        "observed_at": ["2020-01-01", "2020-01-02", "2020-01-03"],
        "amount": [-2.0, 0.0, 3.0], "discount": [np.nan, 1.0, 0.0],
        "category": ["x", "x", "x"],
    })


def detect(df, **overrides):
    options = dict(
        required_columns=tuple(frame().columns),
        required_numeric_columns=("branch_id", "sku_id", "amount"),
        nullable_numeric_columns=("discount",),
        identifier_columns=("branch_id", "sku_id"), nonnull_columns=("category",),
        date_column="observed_at", business_key=("branch_id", "sku_id", "observed_at"),
        issue_details={"source_type": "database", "organization": "reference"},
        field_details={"amount": {"metric": "demand"}},
        sign_fields={"amount": (QualitySeverity.WARNING, QualitySeverity.INFO)},
    )
    options.update(overrides)
    return detect_tabular_quality(df, **options)


def test_non_walmart_fields_source_and_semantics_are_explicit():
    df = frame()
    before = df.copy(deep=True)
    issues = detect(df)
    assert [(i.issue_code, i.field, i.affected_count) for i in issues] == [
        ("NEGATIVE_VALUE", "amount", 1), ("ZERO_VALUE", "amount", 1),
    ]
    assert all(i.details == {"source_type": "database", "organization": "reference", "metric": "demand"} for i in issues)
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize("field,value,code", [
    ("observed_at", "invalid", "INVALID_DATE"),
    ("amount", np.nan, "MISSING_VALUE"),
    ("amount", np.inf, "INVALID_NUMERIC"),
    ("branch_id", 0, "INVALID_DIMENSION"),
    ("sku_id", 1.5, "INVALID_DIMENSION"),
    ("discount", np.inf, "INVALID_NUMERIC"),
    ("category", None, "MISSING_VALUE"),
])
def test_configured_checks_do_not_mutate_inputs(field, value, code):
    df = frame()
    df.loc[0, field] = value
    before = df.copy(deep=True)
    assert code in {i.issue_code for i in detect(df)}
    pd.testing.assert_frame_equal(df, before)


def test_configured_business_key_and_required_fields():
    df = frame()
    df.loc[1, "observed_at"] = df.loc[0, "observed_at"]
    duplicate = next(i for i in detect(df) if i.issue_code == "BUSINESS_KEY_DUPLICATE")
    assert duplicate.affected_count == 2
    missing = next(i for i in detect(df.drop(columns="category")) if i.issue_code == "MISSING_REQUIRED_FIELD")
    assert missing.field == "category"


def test_generic_detection_has_no_default_source_metric_or_sign_checks():
    issues = detect(frame(), issue_details={}, field_details={}, sign_fields={})
    assert issues == []
    df = frame()
    df.loc[0, "amount"] = np.inf
    issues = detect(df, issue_details={}, field_details={}, sign_fields={})
    assert issues[0].details == {}


@pytest.mark.parametrize('override', [
    {'identifier_columns': ('category',)},
    {'required_numeric_columns': ('amount', 'undeclared')},
    {'nullable_numeric_columns': ('amount',)},
    {'sign_fields': {'category': (QualitySeverity.WARNING, QualitySeverity.INFO)}},
    {'required_columns': ('amount', 'amount')},
    {'boolean_columns': ('amount',)},
    {'date_column': 'amount'},
    {'required_columns': 'amount'},
    {'sign_fields': {'amount': (QualitySeverity.WARNING,)}},
])
def test_inconsistent_specs_raise_before_detection(override):
    with pytest.raises(ValueError, match='Invalid detector specification'):
        detect(frame(), **override)


def test_valid_optional_fields_may_be_absent_or_nullable():
    df = frame().drop(columns='discount')
    required = tuple(df.columns)
    issues = detect(df, required_columns=required, nullable_numeric_columns=('discount', 'optional'),
                    boolean_columns=('optional_flag',), nonnull_columns=('category', 'optional_label'))
    assert {i.issue_code for i in issues} == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    df['optional'] = np.nan
    assert {i.issue_code for i in detect(df, required_columns=required, nullable_numeric_columns=('discount', 'optional'))} == {'NEGATIVE_VALUE', 'ZERO_VALUE'}


@pytest.mark.parametrize('values,expected', [
    ([True, False, True], None), ([0.0, 1.0, 0.0], None),
    ([0.0, 2.0, 1.0], 'INVALID_VALUE'),
    ([0.0, np.inf, 1.0], 'INVALID_VALUE'),
    ([True, None, False], 'MISSING_VALUE'),
    (['true', 'false', 'true'], 'INVALID_VALUE'),
])
def test_generic_boolean_check_with_non_walmart_name(values, expected):
    df = frame(); df['enabled'] = values
    before = df.copy(deep=True)
    issues = detect(df, boolean_columns=('enabled',))
    flags = [i for i in issues if i.field == 'enabled']
    if expected is None:
        assert not flags
    else:
        assert expected in {i.issue_code for i in flags}
        assert all(i.details == {'source_type': 'database', 'organization': 'reference'} for i in flags)
    pd.testing.assert_frame_equal(df, before)


def test_optional_numeric_identifier_and_nonnull_checks_are_not_skipped():
    df = frame()
    options = dict(nullable_numeric_columns=('discount', 'optional_id', 'optional_amount'),
                   identifier_columns=('branch_id', 'sku_id', 'optional_id'),
                   nonnull_columns=('category', 'optional_amount'))
    assert {i.issue_code for i in detect(df, **options)} == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    df['optional_id'] = [1.0, np.nan, 2.0]
    df['optional_amount'] = [1.0, np.nan, 2.0]
    issues = detect(df, **options)
    assert [(i.field, i.affected_count) for i in issues if i.issue_code == 'MISSING_VALUE'] == [
        ('optional_id', 1), ('optional_amount', 1),
    ]


def test_boolean_nonnull_declaration_does_not_duplicate_missing_issues():
    df = frame(); df['flag'] = pd.Series([True, None, False], dtype='boolean')
    issues = detect(df, boolean_columns=('flag',), nonnull_columns=('category', 'flag'))
    missing = [i for i in issues if i.issue_code == 'MISSING_VALUE' and i.field == 'flag']
    assert len(missing) == 1 and missing[0].affected_count == 1


def test_optional_identifier_absence_does_not_skip_other_business_key_checks():
    df = frame(); df.loc[1, 'observed_at'] = df.loc[0, 'observed_at']
    issues = detect(df, nullable_numeric_columns=('discount', 'optional_id'),
                    identifier_columns=('branch_id', 'sku_id', 'optional_id'))
    assert 'BUSINESS_KEY_DUPLICATE' in {i.issue_code for i in issues}


def test_business_key_without_date_is_checked_when_optional_date_absent():
    df = frame().drop(columns='observed_at')
    issues = detect(df, required_columns=tuple(df.columns), business_key=('branch_id', 'sku_id'))
    assert 'BUSINESS_KEY_DUPLICATE' in {i.issue_code for i in issues}
