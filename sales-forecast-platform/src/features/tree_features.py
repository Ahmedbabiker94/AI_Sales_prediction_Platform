import pandas as pd


def add_lag_features(df: pd.DataFrame):

    df = df.sort_values("Date")

    df["lag_1"] = (
        df["Weekly_Sales"]
        .shift(1)
    )

    df["lag_2"] = (
        df["Weekly_Sales"]
        .shift(2)
    )

    df["lag_4"] = (
        df["Weekly_Sales"]
        .shift(4)
    )

    df["lag_52"] = (
        df["Weekly_Sales"]
        .shift(52)
    )

    return df


def add_rolling_features(df: pd.DataFrame):

    df["rolling_mean_4"] = (
        df["Weekly_Sales"]
        .rolling(4)
        .mean()
    )

    df["rolling_mean_12"] = (
        df["Weekly_Sales"]
        .rolling(12)
        .mean()
    )

    df["rolling_std_4"] = (
        df["Weekly_Sales"]
        .rolling(4)
        .std()
    )

    return df


def add_trend_features(df: pd.DataFrame):

    df["sales_trend"] = (
        df["Weekly_Sales"]
        .pct_change()
    )

    return df
