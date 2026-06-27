import pandas as pd

from src.services.forecast_history_service import (
    ForecastHistoryService
)

from src.inference.predictor import (
    Predictor
)

from src.features.history_feature_calculator import (
    HistoryFeatureCalculator
)


from src.services.forecast_persistence_service import (
    ForecastPersistenceService
)
from src.providers.postgres_sales_provider import (
    PostgresSalesProvider
)
from src.providers.postgres_forecast_provider import (
    PostgresForecastProvider
)
from src.providers.postgres_sales_provider import (
    PostgresSalesProvider
)

from src.providers.store_feature_provider import (
    StoreFeatureProvider
)
from src.providers.forecast_provider import (
    ForecastProvider
)

from src.core.logger import (
    get_logger
)
import time

# from src.core.metrics import (
#     metrics_service
# )
from src.core.model_monitor import (
    model_monitor
)
from src.providers.postgres_external_features_provider import (
    PostgresExternalFeaturesProvider
)
from src.core.metrics import (
    FORECAST_COUNT,
    FORECAST_DURATION
)

class RecursiveForecastService:

    def __init__(self):

        self.predictor = Predictor(
            model_type="production"
        )

        self.sales_provider = (
            PostgresSalesProvider()
        )

        self.store_provider = (
            StoreFeatureProvider()
        )

        self.forecast_provider = (
            PostgresForecastProvider()
        )

        self.logger = (
            get_logger("forecast")
        )
        self.external_features_provider = (
            PostgresExternalFeaturesProvider()
        )


    def forecast(
        self,
        store,
        dept,
        weeks=4
    ):
        self.logger.info(
            f"Forecast started | Store={store} | Dept={dept} | Weeks={weeks}"
        )
        start_time = time.time()

        history = (
            self.sales_provider
            .get_history(
                store=store,
                dept=dept
            )
        )

        sales = [
            row["weekly_sales"]
            for row in history
        ]

        if len(sales) == 0:
            sales = [0.0] * 52

        forecasts = []

        for _ in range(weeks):

            features = (
                self.build_features_from_sales(
                    sales
                )
            )

            future_row = (
                self.build_prediction_row(
                    store,
                    dept,
                    features
                )
            )

            future_df = pd.DataFrame(
                [future_row]
            )

            prediction = float(
                self.predictor
                .predict_dataframe(
                    future_df
                )[0]
            )
            model_monitor.record_prediction(
                "production"
            )
            self.logger.info(
                f"Forecast generated | Store={store} | Dept={dept} | Value={prediction}"
            )

            forecasts.append(
                prediction
            )

            forecast_date = (
                pd.Timestamp.today()
                +
                pd.Timedelta(
                    days=7 * len(forecasts)
                )
            )

            self.forecast_provider.save_forecast(
                store=store,
                dept=dept,
                forecast_date=forecast_date.date(),
                predicted_sales=prediction,
                model_version="production"
            )
            self.logger.info(
                f"Forecast persisted | Store={store} | Dept={dept} | Date={forecast_date}"
            )
            sales.insert(
                0,
                prediction
            )

            sales = sales[:52]
            self.logger.info(
                f"Forecast completed | Store={store} | Dept={dept}"
            )
            elapsed = (
                time.time()
                -
                start_time
            )

            FORECAST_COUNT.inc()

            FORECAST_DURATION.observe(
                elapsed
            )
        return forecasts

    def build_prediction_row(
        self,
        store,
        dept,
        history_features
    ):

        store_features = (
            self.store_provider
            .get_features(store)
        )
        external_features = (
            self.external_features_provider
            .get_features(
                store,
                dept
            )
        )

        return {

            "Date": pd.Timestamp.today(),

            "Store": store,
            "Dept": dept,

            "Type": store_features["type"],
            "Size": store_features["size"],
            **external_features,


            **history_features
        }

    def build_features_from_sales(
        self,
        sales
    ):

        return (
            HistoryFeatureCalculator
            .calculate(sales)
        )