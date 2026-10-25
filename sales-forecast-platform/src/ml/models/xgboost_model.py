# Lazy constructor hook retained for legacy safe estimator stubs.
# Persistence is fail-closed until a versioned artifact compatibility policy exists.
XGBRegressor = None

from src.ml.models.base_model import (
    BaseForecastModel
)




class XGBoostForecastModel(
    BaseForecastModel
):

    def __init__(self):

        factory = XGBRegressor
        if factory is None:
            from xgboost import XGBRegressor as factory
        self.model = factory(

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

    def save(self, path):
        raise ValueError("legacy persistence unsupported; versioned candidate packaging required")

    def load(self, path):
        raise ValueError("legacy persistence unsupported; original owned candidate required")
