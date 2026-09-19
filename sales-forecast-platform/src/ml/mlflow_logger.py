import mlflow
import mlflow.xgboost


MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"

EXPERIMENT_NAME = "sales_forecasting"


def log_model_to_mlflow(
    model,
    metrics,
    model_type,
    outlier_report_path=None
):

    mlflow.set_tracking_uri(
        MLFLOW_TRACKING_URI
    )

    mlflow.set_experiment(
        EXPERIMENT_NAME
    )

    with mlflow.start_run() as run:

        # Model metadata
        mlflow.set_tag(
            "model_type",
            model_type
        )

        # Metrics
        mlflow.log_metric(
            "mae",
            metrics["mae"]
        )

        mlflow.log_metric(
            "rmse",
            metrics["rmse"]
        )

        mlflow.log_metric(
            "r2",
            metrics["r2"]
        )

        # Outlier report
        if outlier_report_path:

            mlflow.log_artifact(
                outlier_report_path
            )

        # Log model only.
        # Registration is handled by model_registry.py.
        mlflow.xgboost.log_model(
            xgb_model=model.model,
            artifact_path="model"
        )

        return run.info.run_id