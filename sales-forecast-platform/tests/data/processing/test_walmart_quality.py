import numpy as np
import pandas as pd
import pytest

from src.data.processing.walmart_quality import detect_walmart_quality, REQUIRED_COLUMNS


def frame():
    return pd.DataFrame({
        'Store': [1.0, 1.0, 1.0], 'Dept': [1.0, 1.0, 1.0],
        'Date': ['2020-01-03', '2020-01-10', '2020-01-17'],
        'Weekly_Sales': [-12.5, 0.0, 42.0], 'IsHoliday': [False]*3,
        'Type': ['A']*3, **{name: [1.0]*3 for name in REQUIRED_COLUMNS if name not in ('Store','Dept','Date','Weekly_Sales','IsHoliday','Type')},
    })


def codes(df):
    return {issue.issue_code for issue in detect_walmart_quality(df)}


def test_detector_preserves_raw_values_and_index():
    df = frame()
    df.index = [5, 5, 7]
    original = df.copy(deep=True)
    assert codes(df) == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    pd.testing.assert_frame_equal(df, original)


@pytest.mark.parametrize('column', REQUIRED_COLUMNS)
def test_required_columns(column):
    assert 'MISSING_REQUIRED_FIELD' in codes(frame().drop(columns=column))


@pytest.mark.parametrize('value', [None, '', 'not-a-date', '2020-02-30', 123, True])
def test_invalid_dates(value):
    df = frame(); df['Date'] = df['Date'].astype(object); df.loc[0, 'Date'] = value
    assert 'INVALID_DATE' in codes(df)


@pytest.mark.parametrize('column', ['Store', 'Dept', 'Weekly_Sales'])
@pytest.mark.parametrize('value', [np.nan, np.inf, -np.inf, 'invalid', True, 1+2j])
def test_invalid_required_numerics(column, value):
    df = frame(); df[column] = df[column].astype(object); df.loc[0, column] = value
    assert codes(df) & {'MISSING_VALUE', 'INVALID_NUMERIC'}


@pytest.mark.parametrize('column', ['Store', 'Dept'])
@pytest.mark.parametrize('value', [0, -1, 1.5])
def test_invalid_identifiers(column, value):
    df = frame(); df.loc[0, column] = value
    assert 'INVALID_DIMENSION' in codes(df)


def test_business_keys_ignore_sales_and_extra_columns():
    df = frame(); df.loc[1, 'Date'] = df.loc[0, 'Date']
    issue = next(i for i in detect_walmart_quality(df) if i.issue_code == 'BUSINESS_KEY_DUPLICATE')
    assert issue.affected_count == 2


def test_empty_and_duplicate_columns_fail_schema():
    assert 'SCHEMA_FAILURE' in codes(frame().iloc[:0])
    assert 'SCHEMA_FAILURE' in codes(pd.concat([frame(), frame()['Store']], axis=1))


def test_auxiliary_nulls_permitted_but_infinity_rejected():
    df = frame(); df.loc[0, 'MarkDown1'] = np.nan; df.loc[1, 'Temperature'] = np.nan
    assert codes(df) == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    df.loc[2, 'Temperature'] = np.inf
    assert 'INVALID_NUMERIC' in codes(df)


@pytest.mark.parametrize('value,code', [(np.nan, 'MISSING_VALUE'), (np.inf, 'INVALID_NUMERIC'), (-np.inf, 'INVALID_NUMERIC')])
def test_float_numeric_issue_codes_and_counts(value, code):
    df = frame(); df.loc[0, 'Weekly_Sales'] = value
    issue = next(i for i in detect_walmart_quality(df) if i.issue_code == code)
    assert issue.field == 'Weekly_Sales'
    assert issue.affected_count == 1


def test_nullable_integer_identifiers():
    df = frame(); df['Store'] = df['Store'].astype('Int64')
    assert codes(df) == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    df.loc[0, 'Store'] = pd.NA
    assert 'MISSING_VALUE' in codes(df)


def test_exact_negative_and_zero_issue_semantics():
    issues = detect_walmart_quality(frame())
    assert [i.model_dump(mode='json') for i in issues] == [
        dict(issue_code='NEGATIVE_VALUE', issue_type='invalid', severity='warning',
             field='Weekly_Sales', affected_count=1,
             message='NEGATIVE_VALUE: Weekly_Sales (1)', rule_id=None,
             details={'source_type':'csv', 'metric':'sales'}),
        dict(issue_code='ZERO_VALUE', issue_type='invalid', severity='info',
             field='Weekly_Sales', affected_count=1,
             message='ZERO_VALUE: Weekly_Sales (1)', rule_id=None,
             details={'source_type':'csv', 'metric':'sales'}),
    ]


@pytest.mark.parametrize('mutation,expected', [
    ('missing', ('MISSING_REQUIRED_FIELD', 'missing', 'error', 'Dept', 1)),
    ('date', ('INVALID_DATE', 'invalid', 'error', 'Date', 1)),
    ('numeric', ('INVALID_NUMERIC', 'invalid', 'error', 'Weekly_Sales', 1)),
    ('identifier', ('INVALID_DIMENSION', 'invalid', 'error', 'Store', 1)),
    ('duplicate', ('BUSINESS_KEY_DUPLICATE', 'duplicate', 'error', None, 2)),
])
def test_blocking_issue_semantics(mutation, expected):
    df = frame()
    if mutation == 'missing': df = df.drop(columns='Dept')
    elif mutation == 'date': df.loc[1, 'Date'] = 'invalid'
    elif mutation == 'numeric': df.loc[1, 'Weekly_Sales'] = np.inf
    elif mutation == 'identifier': df.loc[1, 'Store'] = 0
    else: df.loc[1, 'Date'] = df.loc[0, 'Date']
    issue = next(i for i in detect_walmart_quality(df) if i.issue_code == expected[0])
    assert (issue.issue_code, issue.issue_type.value, issue.severity.value, issue.field, issue.affected_count) == expected
    assert issue.details == {'source_type':'csv', **({'metric':'sales'} if issue.field == 'Weekly_Sales' else {})}


@pytest.mark.parametrize('values,dtype', [
    ([True, False, True], 'bool'), ([True, False, True], 'boolean'),
    ([0, 1, 0], 'int64'), ([0, 1, 0], 'Int64'),
    ([0.0, 1.0, 0.0], 'float64'),
])
def test_supported_boolean_representations_preserved(values, dtype):
    df = frame(); df['IsHoliday'] = pd.Series(values, dtype=dtype)
    before = df.copy(deep=True)
    assert codes(df) == {'NEGATIVE_VALUE', 'ZERO_VALUE'}
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize('value,expected', [
    (np.nan, 'MISSING_VALUE'), (np.inf, 'INVALID_VALUE'),
    (-np.inf, 'INVALID_VALUE'), (2.0, 'INVALID_VALUE'),
    (-1.0, 'INVALID_VALUE'), (0.5, 'INVALID_VALUE'),
    ('True', 'INVALID_VALUE'), ('0', 'INVALID_VALUE'),
    ('not-a-flag', 'INVALID_VALUE'), (1+0j, 'INVALID_VALUE'),
])
def test_invalid_boolean_values_have_exact_issue_counts(value, expected):
    df = frame()
    # Preserve numeric versus unsupported column types, without coercing input.
    if isinstance(value, (str, complex)):
        df['IsHoliday'] = pd.Series([value, value, value])
        count = 3
    else:
        df['IsHoliday'] = pd.Series([0.0, value, 1.0])
        count = 1
    before = df.copy(deep=True)
    issue = next(i for i in detect_walmart_quality(df) if i.issue_code == expected and i.field == 'IsHoliday')
    assert issue.affected_count == count
    assert issue.severity.value == 'error'
    assert issue.details == {'source_type': 'csv'}
    pd.testing.assert_frame_equal(df, before)
