import pandas as pd
from src.features.history_feature_builder import (
    HistoryFeatureBuilder
)
FEATURE_COLS = [
    "Unnamed: 0",
    "Store",
    "IsHoliday",
    "Dept",
    "Temperature",
    "Fuel_Price",
    "MarkDown1",
    "MarkDown2",
    "MarkDown3",
    "MarkDown4",
    "MarkDown5",
    "CPI",
    "Unemployment",
    "Type",
    "Size",
    "Returns",
    "year",
    "month",
    "week_of_year",
    "quarter",
    "total_markdown",
    "has_promotion",
    "is_super_bowl",
    "is_labor_day",
    "is_thanksgiving",
    "is_christmas",
    "lag_1",
    "lag_2",
    "lag_4",
    "lag_52",
    "rolling_mean_4",
    "rolling_mean_12",
    "rolling_std_4",
    "sales_trend",
]

# =========================================================
# TRAINING FEATURE PIPELINE
# =========================================================

def prepare_features(df):

    df = df.copy()
    df["Date"] = pd.to_datetime(
    df["Date"]
    )
    # -----------------------------------------------------
    # Date Features
    # -----------------------------------------------------

    df["year"] = df["Date"].dt.year

    df["month"] = df["Date"].dt.month

    df["week_of_year"] = (
        df["Date"]
        .dt
        .isocalendar()
        .week
        .astype(int)
    )

    df["quarter"] = df["Date"].dt.quarter

    # -----------------------------------------------------
    # Promotion Features
    # -----------------------------------------------------

    markdown_cols = [
        "MarkDown1",
        "MarkDown2",
        "MarkDown3",
        "MarkDown4",
        "MarkDown5"
    ]

    df[markdown_cols] = (
        df[markdown_cols]
        .fillna(0)
    )

    df["total_markdown"] = (
        df[markdown_cols]
        .sum(axis=1)
    )

    df["has_promotion"] = (
        df["total_markdown"] > 0
    ).astype(int)

    # -----------------------------------------------------
    # Holiday Features
    # -----------------------------------------------------

    df["is_super_bowl"] = 0
    df["is_labor_day"] = 0
    df["is_thanksgiving"] = 0
    df["is_christmas"] = 0

    # -----------------------------------------------------
    # Lag Features
    # -----------------------------------------------------

    df = df.sort_values(
        by=["Store", "Dept", "Date"]
    )

    df["lag_1"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .shift(1)
    )

    df["lag_2"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .shift(2)
    )

    df["lag_4"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .shift(4)
    )

    df["lag_52"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .shift(52)
    )

    # -----------------------------------------------------
    # Rolling Features
    # -----------------------------------------------------

    df["rolling_mean_4"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .transform(
            lambda x: x.shift(1).rolling(4).mean()
        )
    )

    df["rolling_mean_12"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .transform(
            lambda x: x.shift(1).rolling(12).mean()
        )
    )

    df["rolling_std_4"] = (
        df.groupby(["Store", "Dept"])["Weekly_Sales"]
        .transform(
            lambda x: x.shift(1).rolling(4).std()
        )
    )

    # -----------------------------------------------------
    # Trend Feature
    # -----------------------------------------------------

    df["sales_trend"] = (
        df["lag_1"] - df["lag_4"]
    )

    # -----------------------------------------------------
    # Fill Missing Values
    # -----------------------------------------------------

    numeric_cols = df.select_dtypes(
        include=["number"]
    ).columns

    df[numeric_cols] = (
        df[numeric_cols]
        .fillna(0)
    )

    # -----------------------------------------------------
    # Encode Type
    # -----------------------------------------------------

    df["Type"] = (
        df["Type"]
        .astype("category")
        .cat.codes
    )

    # -----------------------------------------------------
    # Final Features
    # -----------------------------------------------------

    feature_columns = [

        "Store",
        "Dept",
        "Type",
        "Size",
        "Temperature",
        "Fuel_Price",
        "CPI",
        "Unemployment",

        "total_markdown",
        "has_promotion",

        "MarkDown1",
        "MarkDown2",
        "MarkDown3",
        "MarkDown4",
        "MarkDown5",

        "IsHoliday",

        "is_super_bowl",
        "is_labor_day",
        "is_thanksgiving",
        "is_christmas",

        "year",
        "month",
        "week_of_year",
        "quarter",

        "lag_1",
        "lag_2",
        "lag_4",
        "lag_52",

        "rolling_mean_4",
        "rolling_mean_12",
        "rolling_std_4",

        "sales_trend"
    ]

    X = df[feature_columns]

    y = df["Weekly_Sales"]

    return X, y


# =========================================================
# INFERENCE FEATURE PIPELINE
# =========================================================

def prepare_inference_features(df):

    df = df.copy()

    # -----------------------------------------------------
    # Date Features
    # -----------------------------------------------------

    df["year"] = df["Date"].dt.year

    df["month"] = df["Date"].dt.month

    df["week_of_year"] = (
        df["Date"]
        .dt
        .isocalendar()
        .week
        .astype(int)
    )

    df["quarter"] = df["Date"].dt.quarter

    # -----------------------------------------------------
    # Promotion Features
    # -----------------------------------------------------

    markdown_cols = [
        "MarkDown1",
        "MarkDown2",
        "MarkDown3",
        "MarkDown4",
        "MarkDown5"
    ]

    df[markdown_cols] = (
        df[markdown_cols]
        .fillna(0)
    )

    df["total_markdown"] = (
        df[markdown_cols]
        .sum(axis=1)
    )

    df["has_promotion"] = (
        df["total_markdown"] > 0
    ).astype(int)

    # -----------------------------------------------------
    # Holiday Features
    # -----------------------------------------------------

    df["is_super_bowl"] = 0
    df["is_labor_day"] = 0
    df["is_thanksgiving"] = 0
    df["is_christmas"] = 0

    # -----------------------------------------------------
    # Validate History Features
    # -----------------------------------------------------

    required_history_cols = [

        "lag_1",
        "lag_2",
        "lag_4",
        "lag_52",

        "rolling_mean_4",
        "rolling_mean_12",
        "rolling_std_4",

        "sales_trend"
    ]

    missing = [

        col
        for col in required_history_cols
        if col not in df.columns

    ]

    if missing:

        raise ValueError(
            f"Missing history features: {missing}"
        )

    # -----------------------------------------------------
    # Encode Type
    # -----------------------------------------------------

    df["Type"] = (
        df["Type"]
        .astype("category")
        .cat.codes
    )

    # -----------------------------------------------------
    # Final Features
    # -----------------------------------------------------

    feature_columns = [

        "Store",
        "Dept",
        "Type",
        "Size",
        "Temperature",
        "Fuel_Price",
        "CPI",
        "Unemployment",

        "total_markdown",
        "has_promotion",

        "MarkDown1",
        "MarkDown2",
        "MarkDown3",
        "MarkDown4",
        "MarkDown5",

        "IsHoliday",

        "is_super_bowl",
        "is_labor_day",
        "is_thanksgiving",
        "is_christmas",

        "year",
        "month",
        "week_of_year",
        "quarter",

        "lag_1",
        "lag_2",
        "lag_4",
        "lag_52",

        "rolling_mean_4",
        "rolling_mean_12",
        "rolling_std_4",

        "sales_trend"
    ]

    return df[feature_columns]
    
from src.features.base_features import (
    add_date_features,
    add_markdown_features,
    add_holiday_features
)

from src.features.tree_features import (
    add_lag_features,
    add_rolling_features,
    add_trend_features
)


def run_feature_pipeline(df):

    df = add_date_features(df)

    df = add_markdown_features(df)

    df = add_holiday_features(df)

    df = add_lag_features(df)

    df = add_rolling_features(df)

    df = add_trend_features(df)

    import numpy as np

# Replace infinite values
    df.replace([np.inf, -np.inf], np.nan, inplace=True)

# Fill NaN values
    df = df.fillna(0)

    return df
