"""Reusable non-mutating tabular detection with explicit schema inputs."""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_complex_dtype, is_numeric_dtype

from src.data.processing.quality import (
    QualityIssue, QualityIssueType, QualitySeverity,
)


def detect_tabular_quality(
    df: pd.DataFrame,
    *,
    required_columns: Sequence[str],
    required_numeric_columns: Sequence[str],
    nullable_numeric_columns: Sequence[str] = (),
    identifier_columns: Sequence[str] = (),
    nonnull_columns: Sequence[str] = (),
    boolean_columns: Sequence[str] = (),
    date_column: str,
    business_key: Sequence[str],
    issue_details: Mapping[str, Any] | None = None,
    field_details: Mapping[str, Mapping[str, Any]] | None = None,
    sign_fields: Mapping[str, tuple[QualitySeverity, QualitySeverity]] | None = None,
) -> list[QualityIssue]:
    """Detect structural/value issues using explicit source schema choices.

    Positive-integral checks apply only to identifier_columns. Numeric epoch
    dates are not accepted by this calendar-date mechanism. Parsed values are
    temporary: inputs are never normalized, repaired, or mutated.
    """
    field_details = field_details or {}
    sign_fields = sign_fields or {}
    # Check declarations, not data: optional fields may still be absent.
    groups = {
        "required_columns": required_columns,
        "required_numeric_columns": required_numeric_columns,
        "nullable_numeric_columns": nullable_numeric_columns,
        "identifier_columns": identifier_columns,
        "nonnull_columns": nonnull_columns,
        "boolean_columns": boolean_columns,
        "business_key": business_key,
    }
    for name, fields in groups.items():
        if isinstance(fields, str) or any(not isinstance(f, str) or not f for f in fields):
            raise ValueError(f"Invalid detector specification: {name} requires field names")
        if len(fields) != len(set(fields)):
            raise ValueError(f"Invalid detector specification: duplicate fields in {name}")
    numeric_fields = set(required_numeric_columns) | set(nullable_numeric_columns)
    if set(required_numeric_columns) & set(nullable_numeric_columns):
        raise ValueError("Invalid detector specification: numeric groups overlap")
    if not set(required_numeric_columns) <= set(required_columns):
        raise ValueError("Invalid detector specification: required numeric fields must be required columns")
    if not set(identifier_columns) <= numeric_fields:
        raise ValueError("Invalid detector specification: identifiers must have numeric checks")
    if not set(sign_fields) <= numeric_fields:
        raise ValueError("Invalid detector specification: sign fields must have numeric checks")
    if set(boolean_columns) & numeric_fields:
        raise ValueError("Invalid detector specification: boolean and numeric checks conflict")
    if not isinstance(date_column, str) or not date_column:
        raise ValueError("Invalid detector specification: date_column requires a field name")
    if date_column in numeric_fields or date_column in boolean_columns:
        raise ValueError("Invalid detector specification: date and numeric/boolean checks conflict")
    for severities in sign_fields.values():
        if not isinstance(severities, Sequence) or len(severities) != 2 or any(not isinstance(v, QualitySeverity) for v in severities):
            raise ValueError("Invalid detector specification: sign checks require two severities")
    issues = []

    def report(code, kind, field=None, count=1, severity=QualitySeverity.ERROR):
        issues.append(QualityIssue(
            issue_code=code, issue_type=kind, severity=severity, field=field,
            affected_count=int(count), message=f"{code}: {field or 'dataset'} ({count})",
            details={**(issue_details or {}), **field_details.get(field, {})},
        ))

    if not df.columns.is_unique:
        report("SCHEMA_FAILURE", QualityIssueType.INVALID)
        return issues
    if df.empty:
        report("SCHEMA_FAILURE", QualityIssueType.INVALID, count=0)
    for field in required_columns:
        if field not in df:
            report("MISSING_REQUIRED_FIELD", QualityIssueType.MISSING, field)

    dates = None
    if date_column in df:
        # Match the downstream parser; do not accept numeric epoch dates.
        try:
            dates = pd.to_datetime(df[date_column], errors="coerce")
            valid_calendar = dates.dt.year.notna()
        except (TypeError, ValueError, AttributeError, OverflowError):
            valid_calendar = pd.Series(False, index=df.index)
        numeric_dates = df[date_column].map(
            lambda value: isinstance(value, (int, float, np.number, bool))
        )
        invalid = ~valid_calendar | numeric_dates
        if invalid.any():
            report("INVALID_DATE", QualityIssueType.INVALID, date_column, invalid.sum())
            dates = None

    valid_ids = True
    for field in tuple(required_numeric_columns) + tuple(nullable_numeric_columns):
        if field not in df:
            if field in identifier_columns and field in business_key:
                valid_ids = False
            continue
        values = df[field]
        required = field in required_numeric_columns or field in identifier_columns or field in nonnull_columns
        missing = values.isna()
        if required and missing.any():
            report("MISSING_VALUE", QualityIssueType.MISSING, field, missing.sum())
        numeric = (
            is_numeric_dtype(values.dtype)
            and not is_bool_dtype(values.dtype)
            and not is_complex_dtype(values.dtype)
        )
        if not numeric:
            if values.notna().any():
                report("INVALID_NUMERIC", QualityIssueType.INVALID, field, values.notna().sum())
            if field in identifier_columns and field in business_key:
                valid_ids = False
            continue
        finite = pd.Series(
            np.isfinite(values.to_numpy(dtype=float, na_value=np.nan)),
            index=df.index,
        )
        invalid = ~missing & ~finite
        if invalid.any():
            report("INVALID_NUMERIC", QualityIssueType.INVALID, field, invalid.sum())
        if field in identifier_columns:
            invalid_id = ~missing & finite & ((values <= 0) | (values % 1 != 0))
            if invalid_id.any():
                report("INVALID_DIMENSION", QualityIssueType.INVALID, field, invalid_id.sum())
            if field in business_key:
                valid_ids = valid_ids and not (missing | invalid | invalid_id).any()
        if field in sign_fields:
            for code, mask, severity in (
                ("NEGATIVE_VALUE", finite & (values < 0), sign_fields[field][0]),
                ("ZERO_VALUE", finite & (values == 0), sign_fields[field][1]),
            ):
                if mask.any():
                    report(code, QualityIssueType.INVALID, field, mask.sum(), severity)

    for field in boolean_columns:
        if field not in df:
            continue  # Optional boolean fields are checked only when present.
        values = df[field]
        missing = values.isna()
        if missing.any():
            report("MISSING_VALUE", QualityIssueType.MISSING, field, missing.sum())
        supported_dtype = is_bool_dtype(values.dtype) or (
            is_numeric_dtype(values.dtype) and not is_complex_dtype(values.dtype)
        )
        if supported_dtype:
            finite = pd.Series(
                np.isfinite(values.to_numpy(dtype=float, na_value=np.nan)),
                index=df.index,
            )
            invalid = ~missing & (~finite | ~values.isin((0, 1)))
        else:
            # In particular, do not normalize strings or object columns.
            invalid = ~missing
        if invalid.any():
            report("INVALID_VALUE", QualityIssueType.INVALID, field, invalid.sum())

    for field in nonnull_columns:
        if field not in numeric_fields and field not in boolean_columns and field in df and df[field].isna().any():
            report("MISSING_VALUE", QualityIssueType.MISSING, field, df[field].isna().sum())
    if (
        (date_column not in business_key or dates is not None) and valid_ids and business_key
        and all(field in df for field in business_key)
    ):
        keys = df[list(business_key)].copy()
        if date_column in business_key:
            keys[date_column] = dates
        duplicates = keys.duplicated(keep=False)
        if duplicates.any():
            report("BUSINESS_KEY_DUPLICATE", QualityIssueType.DUPLICATE, count=duplicates.sum())
    return issues
