import pandas as pd


REQUIRED_COLUMNS = [
    "Store",
    "Dept",
    "Weekly_Sales",
    "Date",
    "IsHoliday"
]


NUMERIC_COLUMNS = [
    "Store",
    "Dept",
    "Weekly_Sales"
]


def validate_required_columns(df: pd.DataFrame):

    missing_columns = [
        col for col in REQUIRED_COLUMNS
        if col not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}"
        )


def validate_missing_values(df: pd.DataFrame):

    missing = df[REQUIRED_COLUMNS].isnull().sum()

    problematic = missing[missing > 0]

    if not problematic.empty:
        raise ValueError(
            f"Missing values detected:\n{problematic}"
        )


def validate_numeric_types(df: pd.DataFrame):

    for col in NUMERIC_COLUMNS:

        if not pd.api.types.is_numeric_dtype(df[col]):
            raise TypeError(
                f"Column {col} must be numeric"
            )




def validate_duplicate_rows(df: pd.DataFrame):

    duplicates = df.duplicated().sum()

    if duplicates > 0:
        raise ValueError(
            f"Duplicate rows detected: {duplicates}"
        )


def validate_dataframe(df: pd.DataFrame):

    print("Starting data validation...")

    validate_required_columns(df)

    validate_missing_values(df)

    validate_numeric_types(df)

    df = create_separate_targets(df)

    validate_duplicate_rows(df)

    print("Data validation passed successfully.")

    return df

def create_separate_targets(df: pd.DataFrame):

    print("Creating separate forecasting targets...")

    df["Returns"] = 0.0

    negative_mask = df["Weekly_Sales"] < 0

    df.loc[negative_mask, "Returns"] = (
        df.loc[negative_mask, "Weekly_Sales"].abs()
    )

    df.loc[negative_mask, "Weekly_Sales"] = 0

    print(
        f"Created Returns target with "
        f"{negative_mask.sum()} return rows"
    )

    return df
