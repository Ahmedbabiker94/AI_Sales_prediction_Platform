# from src.database.history_repository import (
#     HistoryRepository
# )

from src.features.history_feature_builder import (
    HistoryFeatureBuilder
)


class FeatureEnrichmentService:

    def __init__(self):

        self.builder = (
            HistoryFeatureBuilder()
        )

    def enrich(self, df):

        store = int(
            df.iloc[0]["Store"]
        )

        dept = int(
            df.iloc[0]["Dept"]
        )

        history_features = (
            self.builder.build(
                store=store,
                dept=dept
            )
        )

        for key, value in (
            history_features.items()
        ):
            df[key] = value

        return df
