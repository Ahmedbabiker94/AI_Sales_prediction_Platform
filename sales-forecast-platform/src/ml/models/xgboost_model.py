import joblib

from xgboost import XGBRegressor

from src.ml.models.base_model import (
    BaseForecastModel
)
from src.ml.model_registry import load_production_model

class XGBoostForecastModel(BaseForecastModel):

    def __init__(self):

        self.model = XGBRegressor(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=8,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42
        )

    def train(self, X_train, y_train):

        self.model.fit(X_train, y_train)

    def predict(self, X):

        return self.model.predict(X)

    def save(self, path):

        joblib.dump(self.model, path)

    def load(self):

        self.model = load_production_model()

        return self.model
