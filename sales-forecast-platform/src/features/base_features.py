import pandas as pd


def add_date_features(df: pd.DataFrame):

    df["Date"] = pd.to_datetime(df["Date"])

    df["year"] = df["Date"].dt.year

    df["month"] = df["Date"].dt.month

    df["week_of_year"] = (
        df["Date"]
        .dt.isocalendar()
        .week
        .astype(int)
    )

    df["quarter"] = df["Date"].dt.quarter

    return df


def add_markdown_features(df: pd.DataFrame):

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

    return df


def add_holiday_features(df: pd.DataFrame):

    df["is_super_bowl"] = (
        (df["month"] == 2)
    ).astype(int)

    df["is_labor_day"] = (
        (df["month"] == 9)
    ).astype(int)

    df["is_thanksgiving"] = (
        (df["month"] == 11)
    ).astype(int)

    df["is_christmas"] = (
        (df["month"] == 12)
    ).astype(int)

    return df
