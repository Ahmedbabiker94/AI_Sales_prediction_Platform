"""
features.py
-----------
Feature engineering for the sales forecast model.
Converts raw sales CSV rows into model-ready feature vectors.
"""

import pandas as pd
import numpy as np
from pathlib import Path


def load_raw(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values(["Store", "Dept", "Date"]).reset_index(drop=True)
    df["Dept"] = df["Dept"].astype(int)
    
    # Clean noise (negative sales)
    df = df[df["Weekly_Sales"] >= 0].copy()
    
    # Winsorize at 99th percentile per Store+Dept group
    def winsorize_group(x):
        upper = x.quantile(0.99)
        return x.clip(upper=upper)
        
    df["Weekly_Sales"] = df.groupby(["Store", "Dept"])["Weekly_Sales"].transform(winsorize_group)
    return df


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["year"] = df["Date"].dt.year
    df["month"] = df["Date"].dt.month
    df["week_of_year"] = df["Date"].dt.isocalendar().week.astype(int)
    df["quarter"] = df["Date"].dt.quarter
    
    # Holidays
    super_bowl   = pd.to_datetime(["2010-02-12","2011-02-11","2012-02-10","2013-02-08"])
    labor_day    = pd.to_datetime(["2010-09-10","2011-09-09","2012-09-07","2013-09-06"])
    thanksgiving = pd.to_datetime(["2010-11-26","2011-11-25","2012-11-23","2013-11-29"])
    christmas    = pd.to_datetime(["2010-12-31","2011-12-30","2012-12-28","2013-12-27"])

    df["is_super_bowl"]   = df["Date"].isin(super_bowl).astype(int)
    df["is_labor_day"]    = df["Date"].isin(labor_day).astype(int)
    df["is_thanksgiving"] = df["Date"].isin(thanksgiving).astype(int)
    df["is_christmas"]    = df["Date"].isin(christmas).astype(int)
    
    # MarkDowns
    MARKDOWN_COLS = ["MarkDown1", "MarkDown2", "MarkDown3", "MarkDown4", "MarkDown5"]
    for col in MARKDOWN_COLS:
        if col not in df.columns:
            df[col] = 0.0
    
    df["total_markdown"] = df[MARKDOWN_COLS].sum(axis=1)
    df["has_promotion"]  = (df["total_markdown"] > 0).astype(int)
    return df


def add_lag_features(df: pd.DataFrame, lags: list[int] = [1, 2, 4, 52]) -> pd.DataFrame:
    df = df.copy()
    group = df.groupby(["Store", "Dept"])["Weekly_Sales"]
    for lag in lags:
        df[f"lag_{lag}"] = group.shift(lag)
    return df


def add_rolling_features(df: pd.DataFrame, windows: list[int] = [4, 12]) -> pd.DataFrame:
    df = df.copy()
    group = df.groupby(["Store", "Dept"])["Weekly_Sales"]
    
    df["rolling_mean_4"] = group.transform(lambda x: x.shift(1).rolling(4).mean())
    df["rolling_mean_12"] = group.transform(lambda x: x.shift(1).rolling(12).mean())
    df["rolling_std_4"] = group.transform(lambda x: x.shift(1).rolling(4).std())
    
    df["sales_trend"] = df["lag_1"] / (df["rolling_mean_4"] + 1e-8)
    return df


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # LightGBM prefers numerical codes for object/categorical variables if not specified as categorical dtype explicitly.
    # The notebook just used numeric for Store, Dept naturally, and Type, Size were mixed/numeric.
    # If Type and Size are strings like 'A', 'B', map them to ints:
    if "Type" in df.columns and df["Type"].dtype == object:
        df["Type"] = df["Type"].astype("category").cat.codes
    if "Size" in df.columns and df["Size"].dtype == object:
        df["Size"] = df["Size"].astype("category").cat.codes
    if "IsHoliday" in df.columns and df["IsHoliday"].dtype == bool:
        df["IsHoliday"] = df["IsHoliday"].astype(int)
    
    # Store and Dept are already ints, keep them as is.
    return df


FEATURE_COLS = [
    # Store identity
    "Store", "Dept", "Type", "Size",
    # Economic
    "Temperature", "Fuel_Price", "CPI", "Unemployment",
    # Promotions
    "total_markdown", "has_promotion",
    "MarkDown1", "MarkDown2", "MarkDown3", "MarkDown4", "MarkDown5",
    # Holidays
    "IsHoliday", "is_super_bowl", "is_labor_day",
    "is_thanksgiving", "is_christmas",
    # Date
    "year", "month", "week_of_year", "quarter",
    # Lags
    "lag_1", "lag_2", "lag_4", "lag_52",
    # Rolling
    "rolling_mean_4", "rolling_mean_12", "rolling_std_4",
    # Momentum
    "sales_trend",
]
TARGET_COL = "Weekly_Sales"


def build_features(path: str | Path) -> tuple[pd.DataFrame, pd.Series]:
    """Full pipeline: load → engineer → return X, y (dropping NaN rows)."""
    df = load_raw(path)
    df = add_calendar_features(df)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    df = encode_categoricals(df)
    df = df.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    X = df[FEATURE_COLS]
    y = df[TARGET_COL]
    return X, y


def train_test_split_temporal(
    df: pd.DataFrame, test_size: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-aware split: last `test_size` % logic from notebook."""
    # From notebook: train_size = int(len(df) * 0.8)
    train_size_idx = int(len(df) * (1 - test_size))
    # We sort by date just to safely split
    df_sorted = df.sort_values(["Date", "Store", "Dept"]).reset_index(drop=True)
    train = df_sorted.iloc[:train_size_idx]
    test = df_sorted.iloc[train_size_idx:]
    return train, test
