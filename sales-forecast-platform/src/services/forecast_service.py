from src.inference.predictor import Predictor


class ForecastService:

    def __init__(self, model_type="production"):

        self.predictor = Predictor(
            model_type=model_type
        )

    def forecast_dataframe(self, df):

        predictions = self.predictor.predict_dataframe(df)

        return predictions

    def forecast_single(self, row_df):

        prediction = self.predictor.predict_single(row_df)

        return prediction
