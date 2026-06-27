from src.providers.external_features_provider import (
    ExternalFeaturesProvider
)


class PostgresExternalFeaturesProvider(
    ExternalFeaturesProvider
):

    def get_features(
        self,
        store,
        dept
    ):

        return {

            "Temperature": 25.0,
            "Fuel_Price": 3.5,
            "CPI": 220.0,
            "Unemployment": 7.0,

            "MarkDown1": 0.0,
            "MarkDown2": 0.0,
            "MarkDown3": 0.0,
            "MarkDown4": 0.0,
            "MarkDown5": 0.0,

            "IsHoliday": 0
        }