import mlflow
from mlflow.tracking import MlflowClient


MODEL_NAME = "sales_forecasting_model"


def get_production_model_type():

    client = MlflowClient()

    model_version = (
        client.get_model_version_by_alias(
            MODEL_NAME,
            "production"
        )
    )

    run_id = model_version.run_id

    run = client.get_run(run_id)

    model_type = run.data.tags.get(
        "model_type"
    )

    if model_type is None:

        raise ValueError(
            "model_type tag not found "
            "in MLflow run."
        )

    return model_type