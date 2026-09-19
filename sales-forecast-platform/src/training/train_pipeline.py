import pandas as pd
import numpy as np

from xgboost import XGBRegressor

from src.monitoring.outlier_analysis import (
    analyze_prediction_outliers
)

from src.pipelines.data_validation import (
    validate_dataframe
)

from src.data.splitter import (
    train_test_split
)

from src.training.trainer import (
    train_model
)

from src.training.evaluator import (
    evaluate_model
)

from src.training.cross_validation import (
    run_time_series_cv
)

from src.preprocessing.preprocessing_factory import (
    get_preprocessor
)

from src.ml.model_registry import (
    register_model,
    promote_model_to_production
)

from src.ml.mlflow_logger import (
    log_model_to_mlflow
)

from src.deployment.production_artifact_sync import (
    ProductionArtifactSync
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
    model_type: str = "xgboost"

):

    print("Loading data...")

    df = pd.read_csv(
        data_path
    )

    print("Validating data...")

    validate_dataframe(df)

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

    print("Running outlier analysis...")

    outlier_results = (
       analyze_prediction_outliers(
          y_true=y_test,
          y_pred=y_pred,
          original_df=test_df
       )
      )

    run_id = log_model_to_mlflow(
        model=model,
        metrics=metrics,
        model_type=model_type,
        outlier_report_path=(
            outlier_results["report_path"]
        )
    )
    print(
        f"MLflow Run ID: {run_id}"
    )

    version = register_model(
        run_id
    )

    print(
        f"Registered model version: {version}"
    )

    promote_model_to_production(
        version
    )

    sync_result = (
        ProductionArtifactSync()
        .sync()
    )

    print(
        "Production artifact synchronized:"
    )

    print(
        sync_result
    )

    print(
        f"Model version {version} is now @production"
    )

    print(
        "Training pipeline completed."
    )