import mlflow
import mlflow.xgboost

from mlflow.tracking import MlflowClient


EXPERIMENT_NAME = "sales_forecasting"
MODEL_NAME = "sales_forecasting_model"


def log_model_to_mlflow(
    model,
    metrics: dict,
    outlier_report_path=None
):
    """
    Log model, metrics, and artifacts to MLflow.
    Also registers the model into MLflow Model Registry.
    """

    mlflow.set_experiment(EXPERIMENT_NAME)

    with mlflow.start_run() as run:

        run_id = run.info.run_id

        print(f"MLflow Run ID: {run_id}")

        # -----------------------------
        # Log Metrics
        # -----------------------------
        for metric_name, metric_value in metrics.items():

            mlflow.log_metric(
                metric_name,
                float(metric_value)
            )

        # -----------------------------
        # Log Outlier Analysis Artifact
        # -----------------------------
        if outlier_report_path:

            mlflow.log_artifact(
                outlier_report_path
            )

        # -----------------------------
        # Log + Register Model
        # -----------------------------
        mlflow.xgboost.log_model(
            xgb_model=model,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

    # ---------------------------------
    # Retrieve Registered Version
    # ---------------------------------
    client = MlflowClient()

    versions = client.search_model_versions(
        f"name='{MODEL_NAME}'"
    )

    latest_version = sorted(
        versions,
        key=lambda v: int(v.version)
    )[-1]

    print(
        f"Registered Model Version: "
        f"{latest_version.version}"
    )

    return {
        "run_id": run_id,
        "model_name": MODEL_NAME,
        "model_version": latest_version.version,
    }
