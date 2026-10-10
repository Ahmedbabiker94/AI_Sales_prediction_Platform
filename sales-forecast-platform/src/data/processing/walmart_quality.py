"""Schema and detection choices for the Walmart reference training dataset."""

import pandas as pd

from src.data.processing.quality import QualityIssue, QualitySeverity
from src.data.processing.tabular_quality import detect_tabular_quality

REQUIRED_COLUMNS = (
    "Store", "Dept", "Weekly_Sales", "Date", "IsHoliday", "Type", "Size",
    "Temperature", "Fuel_Price", "CPI", "Unemployment",
    "MarkDown1", "MarkDown2", "MarkDown3", "MarkDown4", "MarkDown5",
)
REQUIRED_NUMERIC_COLUMNS = ("Store", "Dept", "Weekly_Sales")
AUXILIARY_NUMERIC_COLUMNS = (
    "Size", "Temperature", "Fuel_Price", "CPI", "Unemployment",
    "MarkDown1", "MarkDown2", "MarkDown3", "MarkDown4", "MarkDown5",
)


def detect_walmart_quality(df: pd.DataFrame) -> list[QualityIssue]:
    """Preserve DQ-01 issue semantics and permitted auxiliary nulls."""
    return detect_tabular_quality(
        df,
        required_columns=REQUIRED_COLUMNS,
        required_numeric_columns=REQUIRED_NUMERIC_COLUMNS,
        nullable_numeric_columns=AUXILIARY_NUMERIC_COLUMNS,
        identifier_columns=("Store", "Dept"),
        nonnull_columns=("Type",),
        boolean_columns=("IsHoliday",),
        date_column="Date",
        business_key=("Store", "Dept", "Date"),
        issue_details={"source_type": "csv"},
        field_details={"Weekly_Sales": {"metric": "sales"}},
        sign_fields={"Weekly_Sales": (QualitySeverity.WARNING, QualitySeverity.INFO)},
    )
