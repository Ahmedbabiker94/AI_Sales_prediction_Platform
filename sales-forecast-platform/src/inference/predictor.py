from src.models.model_factory import get_model
from src.preprocessing.preprocessing_factory import get_preprocessor


class Predictor:

    def __init__(self, model_type="production"):

        self.model_type = model_type

        if model_type == "production":
            resolved_model_type = "xgboost"

        elif model_type == "staging":
            resolved_model_type = "xgboost"

        else:
            resolved_model_type = model_type

        self.model = get_model(resolved_model_type)

        self.preprocessor = get_preprocessor(
            resolved_model_type
        )

        self.load()

    def load(self):

        self.model.load()

    def predict_dataframe(self, df):

        X, _ = self.preprocessor.transform(df)

        predictions = self.model.predict(X)

        return predictions

    def predict_single(self, row_df):

        predictions = self.predict_dataframe(row_df)

        return float(predictions[0])
