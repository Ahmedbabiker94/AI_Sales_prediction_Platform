import pandas as pd


def train_test_split(
    df: pd.DataFrame,
    date_col: str = "Date",
    test_size: float = 0.2,
):
    """
    Chronological train/test split for time-series forecasting.
    """

    df = df.copy()

    df[date_col] = pd.to_datetime(df[date_col])

    df = df.sort_values(date_col)

    split_index = int(len(df) * (1 - test_size))

    train_df = df.iloc[:split_index].copy()
    test_df = df.iloc[split_index:].copy()

    return train_df, test_df
