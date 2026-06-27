from src.inference.predictor import Predictor

from src.services.feature_enrichment_service import (
    FeatureEnrichmentService
)


class ForecastService:

    def __init__(self, model_type="production"):

        self.predictor = Predictor(
            model_type=model_type
        )

        self.feature_service = (
            FeatureEnrichmentService()
        )

    def forecast_dataframe(self, df):

        df = (
            self.feature_service.enrich(df)
        )

        predictions = (
            self.predictor
            .predict_dataframe(df)
        )

        return predictions

    def forecast_single(self, row_df):

        row_df = (
            self.feature_service.enrich(
                row_df
            )
        )

        prediction = (
            self.predictor
            .predict_single(row_df)
        )

        return prediction