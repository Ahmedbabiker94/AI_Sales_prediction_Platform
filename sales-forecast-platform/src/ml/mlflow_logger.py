import mlflow
import mlflow.xgboost


mlflow.set_tracking_uri("sqlite:///mlflow.db")
mlflow.set_experiment("sales_forecasting")



def log_model_to_mlflow(
    model,
    metrics,
    outlier_report_path=None
):

    mlflow.set_tracking_uri("http://127.0.0.1:5000")

    mlflow.set_experiment(
        "sales_forecasting"
    )

    with mlflow.start_run() as run:

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
        if outlier_report_path:
            mlflow.log_artifact(
            outlier_report_path
        )

        mlflow.xgboost.log_model(
            xgb_model=model.model,
            artifact_path="model",
            registered_model_name=
            "sales_forecasting_model"
        )

        return run.info.run_id
