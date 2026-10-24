"""Explicit legacy training only; no tracking, registration, promotion or sync.

This is a compatibility training wrapper over canonical M3 preparation,
not the accepted owned D2 trainer.
Calling it can fit a model. CSV reporting is opt-in; importing does neither.
Production lifecycle operations must be invoked separately. Raw CSV training
is disabled: callers must supply canonical temporal preparation. The wrapper's
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
    tabular_service=None,
    temporal_plan=None,
    feature_configuration=None,
    report_context=None,

):
    """Train supported legacy XGBoost explicitly, returning unregistered results.

    With an explicit canonical service, the compatibility CSV argument is not
    read. Raw-only invocations retain M1 quality admission, then fail closed:
    quality admission alone does not establish temporal preprocessing evidence.
    Unsupported families reject before data access or training imports. No
    registration, stage/alias change, production-pointer write or sync is part
    of this entry point. Pass OutlierReportOptions to opt into bounded CSV export.
    Reporting errors are returned separately and do not discard the fitted model.
    An explicit report_context with matching canonical row index is required;
    canonical dimensions are never guessed to mean Store or Dept.
    """
    if type(model_type) is not str or model_type != "xgboost":
        raise ValueError("unsupported legacy training model family; only xgboost is implemented")

    # Preserve the M1 quality-admission boundary for legacy CSV invocations,
    # but an admitted CSV alone cannot authorize leakage-safe preprocessing.
    if tabular_service is None:
        validate_dataframe(pd.read_csv(data_path))

    # A raw CSV has no authenticated temporal origins, category calibration,
    # release evidence, or frozen state. Do not guess these from target rows.
    from src.services.canonical_tabular_preparation_service import CanonicalTabularPreparationService
    if type(tabular_service) is not CanonicalTabularPreparationService:
        raise ValueError("canonical tabular service, temporal plan and configuration required; raw CSV training is disabled")
    prepared = tabular_service.prepare(temporal_plan, feature_configuration, model_family=model_type)
    X_train, y_train = prepared.partition('training')
    X_test, y_test = prepared.partition('test')
    # Calibration is earlier than every training origin.
    from src.training.trainer import train_model
    from src.training.evaluator import evaluate_model
    # A canonical result cannot be mislabeled as Store/Dept/Date CSV context.
    test_df = report_context
    if outlier_report is not None and (type(test_df) is not pd.DataFrame or not test_df.index.equals(X_test.index)):
        test_df = None
    # Do not invent earlier label-availability evidence for row-index CV.
    # The plan attests training labels at M, not at each hypothetical CV origin.
    # Operators prepare explicit temporal folds instead; no validation labels fit.
    cv_results = {"status": "NOT_RUN", "reason": "use explicit canonical temporal folds"}

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

    outlier_results = {"status": "DISABLED"}
    if outlier_report is not None:
        try:
            from src.monitoring.outlier_analysis import export_outlier_report
            if test_df is None:
                raise ValueError('explicit aligned report context required')
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
        "tabular_preparation_id": prepared.preparation_id,
        "tabular_states": tuple({o.state.state_id:o.state for o in prepared.outputs}.values()),
        "outlier_results": outlier_results,
    }
