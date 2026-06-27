from src.insights import generate_forecast_insights


class InsightsService:

    def get_insights(
        self,
        store=None,
        dept=None
    ):

        return generate_forecast_insights(
            store=store,
            dept=dept
        )