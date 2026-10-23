"""Explicit legacy training only; no tracking, registration, promotion or sync.

This remains the legacy preprocessing/CV path, not the canonical D1/D2 trainer.
Calling it can fit a model. CSV reporting is opt-in; importing does neither.
Production lifecycle operations must be invoked separately. Legacy combined-
partition preprocessing/category leakage is deferred to M3; the legacy wrapper's
save/load format mismatch is deferred to M6. This is not canonical acceptance.
"""
import pandas as pd
import numpy as np

from src.pipelines.data_validation import (
    validate_dataframe
)


def clean_dataframe(df):

    df = df.replace(
        [np.inf, -np.inf],
        np.nan
    )

    df = df.fillna(0)

    return df


def prepare_features(df):

    target_column = "Weekly_Sales"

    feature_columns = [
        col for col in df.columns
        if col not in [
            "Date",
            target_column
        ]
    ]

    X = df[feature_columns]

    y = df[target_column]

    return X, y


def run_training_pipeline(
    data_path: str,
    model_type: str = "xgboost",
    *,
    outlier_report=None,

):
    """Train supported legacy XGBoost explicitly, returning unregistered results.

    Unsupported families reject before data access or training imports. No
    registration, stage/alias change, production-pointer write or sync is part
    of this entry point. Pass OutlierReportOptions to opt into bounded CSV export.
    Reporting errors are returned separately and do not discard the fitted model.
    """
    if type(model_type) is not str or model_type != "xgboost":
        raise ValueError("unsupported legacy training model family; only xgboost is implemented")

    print("Loading data...")

    df = pd.read_csv(
        data_path
    )

    print("Validating data...")

    # Admission must finish before splitting, preprocessing, or training side effects.
    validate_dataframe(df)

    # Defer training dependencies until explicit invocation and admission. In
    # particular, module import must not initialize registry or database clients.
    from xgboost import XGBRegressor
    from src.data.splitter import train_test_split
    from src.training.trainer import train_model
    from src.training.evaluator import evaluate_model
    from src.training.cross_validation import run_time_series_cv
    from src.preprocessing.preprocessing_factory import get_preprocessor

    print(
        "Splitting data chronologically..."
    )

    train_df, test_df = (
        train_test_split(df)
    )

    print(
    "Running feature engineering "
        "with historical context..."
    )

    preprocessor = get_preprocessor(model_type)

    # Keep the original indexes so we can separate
    # train and test after feature engineering.
    train_indices = train_df.index
    test_indices = test_df.index

    # Combine train and test chronologically so that
    # test rows can use previous train/test observations
    # for lag and rolling features.
    combined_df = pd.concat(
        [train_df, test_df]
    ).sort_values(
        by=["Store", "Dept", "Date"]
    )

    # Build all history-dependent features once.
    X_all, y_all = preprocessor.fit_transform(
        combined_df
    )

    # Separate train and test again using their original indexes.
    X_train = X_all.loc[train_indices]
    y_train = y_all.loc[train_indices]

    X_test = X_all.loc[test_indices]
    y_test = y_all.loc[test_indices]
    print(
        "Cleaning NaN and infinite values..."
    )

    train_df = clean_dataframe(
        train_df
    )

    test_df = clean_dataframe(
        test_df
    )

    print(
        "Running TimeSeries "
        "Cross Validation..."
    )

    cv_results = run_time_series_cv(
        model_class=XGBRegressor,
        X=X_train,
        y=y_train,
        n_splits=5
    )

    print(
        "\nCross Validation Results:"
    )

    print(cv_results)

    print("Training model...")

    model = train_model(
        X_train,
        y_train,
        model_type=model_type
    )    
    print("Generating predictions...")

    y_pred = model.predict(X_test)

    print("Evaluating model...")

    metrics = evaluate_model(
        y_test,
        y_pred
    )
    print(metrics)

    #metrics = evaluate_model(model, X_test, y_test)

    #print(metrics)

    outlier_results = {"status": "DISABLED"}
    if outlier_report is not None:
        try:
            from src.monitoring.outlier_analysis import export_outlier_report
            outlier_results = export_outlier_report(y_test, y_pred, test_df, outlier_report)
        except Exception:
            # No raw report/input/path or exception diagnostics escape here.
            # Training succeeded even if report construction, I/O or cleanup failed.
            outlier_results = {"status": "ERROR", "error": "outlier report export failed"}

    print(
        "Training pipeline completed."
    )
    return {
        "model": model,
        "model_type": model_type,
        "metrics": metrics,
        "cv_results": cv_results,
        "outlier_results": outlier_results,
    }
