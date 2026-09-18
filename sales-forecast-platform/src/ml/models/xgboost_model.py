import mlflow.xgboost
import joblib

from xgboost import XGBRegressor

from src.ml.models.base_model import (
    BaseForecastModel
)

from src.ml.model_registry import (
    get_production_model_uri
)


class XGBoostForecastModel(
    BaseForecastModel
):

    def __init__(self):

        self.model = XGBRegressor(

            n_estimators=300,

            learning_rate=0.05,

            max_depth=8,

            subsample=0.8,

            colsample_bytree=0.8,

            random_state=42

        )

    def train(
        self,
        X_train,
        y_train
    ):

        self.model.fit(
            X_train,
            y_train
        )

    def predict(
        self,
        X
    ):

        return self.model.predict(
            X
        )

    def save(
        self,
        path
    ):

        joblib.dump(
            self.model,
            path
        )

    def load(self):

        model_uri = (
            get_production_model_uri()
        )

        self.model = (
            mlflow.xgboost.load_model(
                model_uri
            )
        )

        return self.model