from src.ml.models.xgboost_model import (
    XGBoostForecastModel
)


def get_model(model_type: str):

    if model_type == "xgboost":
        return XGBoostForecastModel()

    raise ValueError(
        f"Unsupported model type: {model_type}"
    )

from src.ml.models.xgboost_model import (
    XGBoostForecastModel
)


def get_model(model_type: str):

    if model_type == "xgboost":

        return XGBoostForecastModel()

    raise ValueError(
        f"Unsupported model type: {model_type}"
    )
