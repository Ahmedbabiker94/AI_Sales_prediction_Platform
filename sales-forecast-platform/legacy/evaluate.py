"""
evaluate.py
-----------
Standalone evaluation script — loads a saved model from MLflow registry
and produces a detailed evaluation report on held-out test data.

Usage:
    python src/evaluate.py --data data/walmart_cleaned.csv --model-uri models:/sales_forecast_lgbm/Production
"""

import argparse
import sys
from pathlib import Path

import mlflow.sklearn
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sys.path.insert(0, str(Path(__file__).parent))
from features import (
    load_raw,
    add_calendar_features,
    add_lag_features,
    add_rolling_features,
    encode_categoricals,
    train_test_split_temporal,
    FEATURE_COLS,
    TARGET_COL,
)


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate sales forecast model")
    p.add_argument("--data", default="data/walmart_cleaned.csv")
    p.add_argument(
        "--model-uri",
        default="models:/sales_forecast_lgbm/Production",
        help="MLflow model URI",
    )
    p.add_argument("--test-size", type=float, default=0.2)
    return p.parse_args()


def load_test_data(data_path: str, test_size: float):
    df_raw = load_raw(data_path)
    subset = add_calendar_features(df_raw)
    subset = add_lag_features(subset)
    subset = add_rolling_features(subset)
    subset = encode_categoricals(subset)
    subset = subset.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    _, test_df = train_test_split_temporal(subset, test_size=test_size)
    return test_df


def print_metrics(y_true, y_pred, label="TEST"):
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)
    mape = np.mean(np.abs((y_true - y_pred) / np.clip(y_true, 1, None))) * 100

    print(f"\n{'─'*60}")
    print(f"  {label} METRICS")
    print(f"{'─'*60}")
    print(f"  MAE   : {mae:.4f}")
    print(f"  RMSE  : {rmse:.4f}")
    print(f"  R²    : {r2:.4f}")
    print(f"  MAPE  : {mape:.2f}%")
    print(f"{'─'*60}\n")
    return {"mae": mae, "rmse": rmse, "r2": r2, "mape": mape}


def main():
    args = parse_args()
    mlflow.set_tracking_uri("file:./mlruns")

    print(f"Loading model from: {args.model_uri}")
    try:
        model = mlflow.sklearn.load_model(args.model_uri)
    except Exception as e:
        print(f"❌ Could not load model: {e}")
        sys.exit(1)

    test_df = load_test_data(args.data, args.test_size)
    X_test = test_df[FEATURE_COLS]
    y_test = test_df[TARGET_COL]

    preds = model.predict(X_test)
    metrics = print_metrics(y_test, preds)

    # Per-store breakdown
    test_df["predicted"] = preds
    per_store = (
        test_df.groupby("Store")
        .apply(
            lambda g: pd.Series(
                {
                    "mae": mean_absolute_error(g[TARGET_COL], g["predicted"]),
                    "rmse": np.sqrt(mean_squared_error(g[TARGET_COL], g["predicted"])),
                    "r2": r2_score(g[TARGET_COL], g["predicted"]),
                }
            )
        )
        .round(4)
    )
    print("\nPer-store breakdown:")
    print(per_store.to_string())

    return metrics


if __name__ == "__main__":
    main()
