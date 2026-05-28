from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)

import numpy as np


def run_time_series_cv(
    model_class,
    X,
    y,
    n_splits=5
):
    """
    Perform rolling TimeSeries cross validation.
    """

    tscv = TimeSeriesSplit(n_splits=n_splits)

    mae_scores = []
    rmse_scores = []
    r2_scores = []

    split_number = 1

    for train_index, test_index in tscv.split(X):

        print(f"\nRunning Fold {split_number}")

        X_train = X.iloc[train_index]
        X_test = X.iloc[test_index]

        y_train = y.iloc[train_index]
        y_test = y.iloc[test_index]

        model = model_class()

        model.fit(X_train, y_train)

        predictions = model.predict(X_test)

        mae = mean_absolute_error(y_test, predictions)

        rmse = np.sqrt(
            mean_squared_error(
                y_test,
                predictions
            )
        )

        r2 = r2_score(
            y_test,
            predictions
        )

        mae_scores.append(mae)
        rmse_scores.append(rmse)
        r2_scores.append(r2)

        print(f"MAE: {mae}")
        print(f"RMSE: {rmse}")
        print(f"R²: {r2}")

        split_number += 1

    return {
        "avg_mae": np.mean(mae_scores),
        "avg_rmse": np.mean(rmse_scores),
        "avg_r2": np.mean(r2_scores)
    }
