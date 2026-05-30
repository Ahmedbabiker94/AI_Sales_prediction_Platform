import mlflow

mlflow.set_tracking_uri(
    "http://127.0.0.1:5000"
)
import mlflow.xgboost
from mlflow.tracking import MlflowClient

MODEL_NAME = "sales_forecasting_model"

client = MlflowClient()


def register_model(run_id: str):

    model_uri = f"runs:/{run_id}/model"

    registered_model = mlflow.register_model(
        model_uri=model_uri,
        name=MODEL_NAME
    )

    return registered_model.version


def promote_model_to_production(version: int):

    client.transition_model_version_stage(
        name=MODEL_NAME,
        version=version,
        stage="Production",
        archive_existing_versions=True
    )

    print(f"Model version {version} promoted to Production")


def promote_model_to_staging(version: int):

    client.transition_model_version_stage(
        name=MODEL_NAME,
        version=version,
        stage="Staging"
    )

    print(f"Model version {version} promoted to Staging")



def load_production_model():

    model_uri = (
        "models:/sales_forecasting_model@production"
    )

    model = mlflow.xgboost.load_model(
        model_uri
    )

    return model

def load_staging_model():

    model_uri = f"models:/{MODEL_NAME}/Staging"

    model = mlflow.xgboost.load_model(model_uri)

    return model


def load_model_version(version: int):

    model_uri = f"models:/{MODEL_NAME}/{version}"

    model = mlflow.xgboost.load_model(model_uri)

    return model
