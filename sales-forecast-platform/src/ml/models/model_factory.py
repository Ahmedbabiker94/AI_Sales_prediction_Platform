from src.ml.models.xgboost_model import (
    XGBoostForecastModel
)

from src.ml.models.lightgbm_model import (
    LightGBMForecastModel
)

from src.ml.models.transformer_model import (
    TransformerForecastModel
)

# --------------------------------------------------
# MODEL REGISTRY
# --------------------------------------------------

MODEL_REGISTRY = {
    "xgboost": XGBoostForecastModel,
    "lightgbm": LightGBMForecastModel,
    "transformer": TransformerForecastModel,
}


# --------------------------------------------------
# FACTORY
# --------------------------------------------------

def get_model(model_type: str):

    model_class = MODEL_REGISTRY.get(
        model_type
    )

    if model_class is None:

        raise ValueError(
            f"Unsupported model type: {model_type}"
        )

    return model_class()
