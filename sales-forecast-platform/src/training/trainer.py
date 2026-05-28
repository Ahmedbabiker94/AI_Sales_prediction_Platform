from src.ml.models.model_factory import (
    get_model
)


def train_model(
    X_train,
    y_train,
    model_type="xgboost"
):

    model = get_model(model_type)

    model.train(X_train, y_train)

    return model
