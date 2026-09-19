from src.ml.models.model_factory import get_model
from src.preprocessing.preprocessing_factory import get_preprocessor

from src.services.feature_enrichment_service import (
    FeatureEnrichmentService
)


class Predictor:

    def __init__(self, model_type="production"):

        self.model_type = model_type

        model_path = None

        if model_type == "production":

            from src.ml.production_resolver import (
                get_production_model_type,
                get_production_model_artifact_path
            )

            resolved_model_type = (
                get_production_model_type()
            )

            model_path = (
                get_production_model_artifact_path()
            )

        else:

            resolved_model_type = model_type

        self.model = get_model(
            resolved_model_type
        )

        self.preprocessor = get_preprocessor(
            resolved_model_type
        )

        self.feature_enrichment_service = (
            FeatureEnrichmentService()
        )

        self.load(
            path=model_path
        )

    def load(self, path=None):

        if path is None:

            raise ValueError(
                "A local model artifact path "
                "is required for model loading."
            )

        return self.model.load(
            path
        )

    def predict_dataframe(self, df):

        enriched_df = (
            self.feature_enrichment_service.enrich(df)
        )

        X = self.preprocessor.transform(
            enriched_df
        )

        predictions = self.model.predict(X)

        return predictions

    def predict_single(self, row_df):

        predictions = self.predict_dataframe(
            row_df
        )

        return float(
            predictions[0]
        )