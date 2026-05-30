from src.ml.models.base_model import BaseForecastModel


class LightGBMForecastModel(
    BaseForecastModel
):

    def train(
        self,
        X_train,
        y_train
    ):
        raise NotImplementedError(
            "LightGBM not implemented yet."
        )

    def predict(
        self,
        X
    ):
        raise NotImplementedError(
            "LightGBM not implemented yet."
        )

    def save(
        self
    ):
        pass

    def load(
        self
    ):
        pass
