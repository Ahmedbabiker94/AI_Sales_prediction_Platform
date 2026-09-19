MODEL_REGISTRY = {
    "xgboost": "src.ml.models.xgboost_model.XGBoostForecastModel",
    "lightgbm": "src.ml.models.lightgbm_model.LightGBMForecastModel",
    "transformer": "src.ml.models.transformer_model.TransformerForecastModel",
}


def _load_model_class(model_path: str):

    module_path, class_name = model_path.rsplit(
        ".",
        1
    )

    module = __import__(
        module_path,
        fromlist=[class_name]
    )

    return getattr(
        module,
        class_name
    )


def get_model(model_type: str):

    model_path = MODEL_REGISTRY.get(
        model_type
    )

    if model_path is None:

        raise ValueError(
            f"Unsupported model type: {model_type}"
        )

    model_class = _load_model_class(
        model_path
    )

    return model_class()