"""
train.py
--------
Training entry-point for the sales forecast model.
Logs parameters, metrics, and artifacts to MLflow (file backend mlruns/).
After training, promotes the best-performing run to "Production" in MLflow Model Registry.

Usage:
    python src/train.py --data data/walmart_cleaned.csv --experiment sales_forecast
"""

import argparse
import sys
from pathlib import Path

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from mlflow.tracking import MlflowClient

# Allow importing sibling modules when called from project root
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

MODEL_NAME = "sales_forecast_lgbm"

# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train sales forecast model")
    p.add_argument("--data", default="data/walmart_cleaned.csv", help="Path to sales CSV")
    p.add_argument("--experiment", default="sales_forecast", help="MLflow experiment name")
    p.add_argument("--test-size", type=float, default=0.2, help="Fraction for holdout test set")
    p.add_argument("--n-estimators", type=int, default=500)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--num-leaves", type=int, default=63)
    p.add_argument("--max-depth", type=int, default=-1)
    p.add_argument("--promote", action="store_true", help="Promote best run to Production")
    return p.parse_args()


# ────────────────────────────────────────────────────────────────────────────
# Data prep
# ────────────────────────────────────────────────────────────────────────────
def prepare_data(data_path: str, test_size: float):
    df_raw = load_raw(data_path)
    
    # Engineer ALL data before splitting so lags don't produce NaNs in test
    subset = add_calendar_features(df_raw)
    subset = add_lag_features(subset)
    subset = add_rolling_features(subset)
    subset = encode_categoricals(subset)
    subset = subset.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    
    train_df, test_df = train_test_split_temporal(subset, test_size=test_size)
    
    X_train, y_train = train_df[FEATURE_COLS], train_df[TARGET_COL]
    X_test, y_test = test_df[FEATURE_COLS], test_df[TARGET_COL]
    
    print(f"Train: {len(X_train):,} rows | Test: {len(X_test):,} rows")
    return X_train, y_train, X_test, y_test


# ────────────────────────────────────────────────────────────────────────────
# Metrics
# ────────────────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_pred) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)
    mape = np.mean(np.abs((y_true - y_pred) / np.clip(y_true, 1, None))) * 100
    return {"mae": mae, "rmse": rmse, "r2": r2, "mape": mape}


# ────────────────────────────────────────────────────────────────────────────
# Promotion helper
# ────────────────────────────────────────────────────────────────────────────
def promote_best_run(experiment_name: str, model_name: str):
    """Find the run with lowest RMSE in the experiment and stage it to Production."""
    client = MlflowClient()
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        print("No experiment found, skipping promotion.")
        return

    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["metrics.rmse ASC"],
        max_results=1,
    )
    if not runs:
        print("No runs found, skipping promotion.")
        return

    best_run = runs[0]
    run_id = best_run.info.run_id
    best_rmse = best_run.data.metrics.get("rmse", float("inf"))
    print(f"Best run: {run_id}  RMSE={best_rmse:.4f}")

    model_uri = f"runs:/{run_id}/model"
    try:
        mv = mlflow.register_model(model_uri=model_uri, name=model_name)
        client.transition_model_version_stage(
            name=model_name,
            version=mv.version,
            stage="Production",
            archive_existing_versions=True,
        )
        print(f"✅ Model v{mv.version} promoted to Production in registry '{model_name}'")
    except Exception as e:
        print(f"⚠️  Registration failed (MLflow server may not be running): {e}")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    # Point MLflow at local file backend
    mlflow.set_tracking_uri("file:./mlruns")
    mlflow.set_experiment(args.experiment)

    X_train, y_train, X_test, y_test = prepare_data(args.data, args.test_size)

    params = {
        "n_estimators": args.n_estimators,
        "learning_rate": args.learning_rate,
        "num_leaves": args.num_leaves,
        "max_depth": args.max_depth,
        "random_state": 42,
        "n_jobs": -1,
        "verbosity": -1,
    }

    with mlflow.start_run() as run:
        mlflow.log_params(params)
        mlflow.log_param("test_size", args.test_size)
        mlflow.log_param("train_rows", len(X_train))
        mlflow.log_param("test_rows", len(X_test))

        model = LGBMRegressor(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[],
        )

        train_preds = model.predict(X_train)
        test_preds = model.predict(X_test)

        train_metrics = {f"train_{k}": v for k, v in compute_metrics(y_train, train_preds).items()}
        test_metrics = compute_metrics(y_test, test_preds)

        mlflow.log_metrics({**train_metrics, **test_metrics})
        mlflow.sklearn.log_model(model, artifact_path="model")

        print(f"\n{'─'*50}")
        print(f"Run ID  : {run.info.run_id}")
        print(f"Train MAE : {train_metrics['train_mae']:.2f}  RMSE: {train_metrics['train_rmse']:.2f}  R²: {train_metrics['train_r2']:.4f}")
        print(f"Test  MAE : {test_metrics['mae']:.2f}  RMSE: {test_metrics['rmse']:.2f}  R²: {test_metrics['r2']:.4f}  MAPE: {test_metrics['mape']:.2f}%")
        print(f"{'─'*50}\n")

    if args.promote:
        promote_best_run(args.experiment, MODEL_NAME)


if __name__ == "__main__":
    main()
